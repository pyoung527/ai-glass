"""On-demand account quota queries. Never issue model-generation requests."""
import json
import os
from pathlib import Path
import selectors
import queue
import threading
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collector import DATA, HOME, read_json, windows, records
from app_paths import WINDOWS
import agent_runtime
if not WINDOWS:
    import pty
from scripts.statusline_bridge import write_json


class RateLimited(Exception):
    pass


class LoginRequired(Exception):
    pass


def executable(name):
    return agent_runtime.command(name)[0]


def codex():
    process = subprocess.Popen(agent_runtime.command('codex','app-server','--stdio'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,**agent_runtime.background_options())
    responses=queue.Queue(maxsize=32)
    def read_responses():
        try:
            while True:
                line=process.stdout.readline(2*1024*1024)
                if not line: break
                responses.put_nowait(json.loads(line))
        except (ValueError,OSError,queue.Full):
            pass
    reader=threading.Thread(target=read_responses,daemon=True)
    reader.start()
    try:
        requests = [dict(id=1,method='initialize',params={'clientInfo':{'name':'ai_glass','version':'1.0'},'capabilities':{}}),dict(method='initialized',params={}),dict(id=2,method='account/rateLimits/read',params={})]
        for request in requests:
            process.stdin.write((json.dumps(request)+'\n').encode())
        process.stdin.flush()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try: response=responses.get(timeout=.5)
            except queue.Empty:
                if process.poll() is not None: break
                continue
            if response.get('id')!=2: continue
            if response.get('error'):
                message=str(response['error']).lower()
                if any(word in message for word in ('login','auth','401')): raise LoginRequired()
                raise RuntimeError('Codex 한도 조회가 거부됐습니다.')
            result=response['result']
            rate=(result.get('rateLimitsByLimitId') or {}).get('codex') or result.get('rateLimits') or {}
            return {key:{'used_percent':value['usedPercent'],'window_minutes':value.get('windowDurationMins'),'resets_at':value.get('resetsAt')}
                    for key,value in rate.items() if key in ('primary','secondary') and isinstance(value,dict)}
        raise TimeoutError()
    finally:
        agent_runtime.stop(process)
        reader.join(timeout=2)
        process.stdin.close(); process.stdout.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def claude_workspace():
    home=Path(os.getenv('CLAUDE_CONFIG_DIR',HOME/'.claude'))
    try:
        logs=sorted((home/'projects').glob('*/*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)
        for path in logs[:10]:
            for record in records(path):
                cwd=record.get('cwd')
                if isinstance(cwd,str) and Path(cwd).is_dir(): return cwd
    except OSError: pass
    return str(HOME)


def refresh_claude_auth(path):
    # Let Claude own token rotation and credential locking. /usage is local UI.
    if WINDOWS:
        return refresh_claude_windows(path)
    master,slave=pty.openpty()
    process=None
    try:
        process=subprocess.Popen([executable('claude'),'--ax-screen-reader',
            '--setting-sources','','--settings','{"disableAllHooks":true}',
            '--strict-mcp-config','/usage'],stdin=slave,stdout=slave,stderr=slave,
            cwd=claude_workspace(),start_new_session=True)
        os.close(slave); slave=None
        deadline=time.monotonic()+35
        with selectors.DefaultSelector() as selector:
            selector.register(master,selectors.EVENT_READ)
            while time.monotonic()<deadline:
                try:
                    oauth=read_json(path,{}).get('claudeAiOauth',{})
                    if oauth.get('accessToken') and oauth.get('expiresAt',0)>time.time()*1000+60000:
                        return
                except (OSError,ValueError):
                    pass  # CLI may be replacing the credential file.
                if selector.select(timeout=.2):
                    try: os.read(master,65536)  # Discard terminal output, never log auth data.
                    except OSError: break
                if process.poll() is not None: break
        raise RuntimeError('Claude 인증 자동 갱신을 완료하지 못했습니다 · CLI /usage를 확인하세요.')
    finally:
        if process is not None:
            try: os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL); process.wait()
        os.close(master)
        if slave is not None: os.close(slave)


def refresh_claude_windows(path):
    from winpty import PtyProcess
    process=PtyProcess.spawn(subprocess.list2cmdline(agent_runtime.command('claude',
        '--settings','{"disableAllHooks":true}','--strict-mcp-config','/usage')),cwd=claude_workspace())
    # ConPTY must be drained while Claude rotates its own credentials.
    def drain():
        try:
            while process.isalive(): process.read(65536)
        except (EOFError,OSError): pass
    thread=threading.Thread(target=drain,daemon=True); thread.start()
    try:
        deadline=time.monotonic()+35
        while time.monotonic()<deadline and process.isalive():
            try:
                oauth=read_json(path,{}).get('claudeAiOauth',{})
                if oauth.get('accessToken') and oauth.get('expiresAt',0)>time.time()*1000+60000: return
            except (OSError,ValueError): pass
            time.sleep(.2)
        raise RuntimeError('Claude 인증 갱신을 완료하지 못했습니다 · CLI에서 /usage를 실행하세요.')
    finally:
        process.close(force=True)
        thread.join(timeout=2)


def claude():
    path=Path(os.getenv('CLAUDE_SECURESTORAGE_CONFIG_DIR',os.getenv('CLAUDE_CONFIG_DIR',HOME/'.claude')) or HOME/'.claude')/'.credentials.json'
    try:
        oauth=read_json(path,{}).get('claudeAiOauth',{})
    except (OSError,ValueError,AttributeError):
        raise LoginRequired() from None
    if not isinstance(oauth.get('accessToken'),str) or not oauth['accessToken']:
        raise LoginRequired()
    if oauth.get('expiresAt',0)<=time.time()*1000+60000:
        refresh_claude_auth(path)
        oauth=read_json(path,{}).get('claudeAiOauth',{})
    token=oauth.get('accessToken')
    if not isinstance(token,str) or not token: raise LoginRequired()
    request=urllib.request.Request('https://api.anthropic.com/api/oauth/usage',headers={'Authorization':'Bearer '+token,'anthropic-beta':'oauth-2025-04-20','Accept':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=15) as response:
            result=json.loads(response.read(1024*1024))
    except urllib.error.HTTPError as error:
        if error.code in (401,403): raise LoginRequired() from None
        if error.code==429: raise RateLimited() from None
        raise RuntimeError(f'사용량 서버 오류 (HTTP {error.code})') from None
    return {key:{'used_percentage':value.get('utilization'),'resets_at':value.get('resets_at')}
            for key,value in result.items() if key in ('five_hour','seven_day') and isinstance(value,dict)}


def parse_antigravity(text):
    rates={}
    names={'Gemini Models':'gemini-weekly','Claude and GPT models':'3p-weekly'}
    for line in text.splitlines():
        parts=line.split('\t')
        if len(parts)!=4 or parts[0] not in names or parts[1]!='Weekly Limit Remaining': continue
        remaining=float(parts[2].removesuffix('%'))
        if not 0<=remaining<=100: raise ValueError('잘못된 잔여량')
        rates[names[parts[0]]]={'used_percent':100-remaining,'resets_at':parts[3]}
    if len(rates)!=2: raise ValueError('Antigravity 사용량 응답 형식을 확인하세요.')
    return rates


def recover_antigravity_keyring():
    marker=Path(os.getenv('ANTIGRAVITY_CONFIG_DIR',HOME/'.gemini/antigravity-cli'))/'cache/antigravity-keyring-unavailable'
    if not marker.exists(): return
    try:
        result=subprocess.run(['gdbus','call','--session','--dest','org.freedesktop.secrets',
            '--object-path','/org/freedesktop/secrets/collection/login','--method',
            'org.freedesktop.DBus.Properties.Get','org.freedesktop.Secret.Collection','Locked'],
            capture_output=True,text=True,timeout=3)
        if result.returncode==0 and result.stdout.strip()=='(<false>,)':
            marker.unlink(missing_ok=True)  # Disposable timeout cache; credentials are untouched.
    except (OSError,subprocess.TimeoutExpired):
        pass


def antigravity():
    if not WINDOWS: recover_antigravity_keyring()
    # /usage is a CLI-handled read-only slash command, not a model prompt.
    result=subprocess.run(agent_runtime.command('agy','--print','/usage'),stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20,cwd=str(HOME),**agent_runtime.background_options())
    if result.returncode:
        if any(word in result.stderr.lower() for word in ('auth','login','401','unauthorized')): raise LoginRequired()
        raise RuntimeError('Antigravity 사용량 조회 실패 · CLI 연결을 확인하세요.')
    return parse_antigravity(result.stdout)


def query(name):
    path=DATA/f'{name}-remote.json'
    try: previous=read_json(path,{})
    except (ValueError,OSError): previous={}
    if previous.get('retryAt',0)>time.time():
        return previous
    try:
        rates={'codex':codex,'claude':claude,'antigravity':antigravity}[name]()
        stamp=time.time()
        parsed=windows(rates,stamp,'서버 사용량 조회',now=stamp)
        if not parsed or any(w['remaining'] is None for w in parsed):
            raise ValueError('유효한 최신 한도를 받지 못했습니다.')
        result={'state':'ok','message':'최신 사용량 조회 완료','windows':parsed,'checkedAt':stamp}
    except RateLimited:
        result={'state':'error','message':'요청 제한 · 1분 후 다시 조회하세요.','retryAt':time.time()+60}
    except LoginRequired:
        result={'state':'login','message':'로그인 필요 · CLI에서 로그인 후 다시 조회하세요.'}
    except (TimeoutError,subprocess.TimeoutExpired):
        result={'state':'error','message':'조회 시간 초과 · 이전 값 유지'}
    except urllib.error.URLError:
        result={'state':'error','message':'서버 연결 실패 · 네트워크를 확인하세요.'}
    except Exception as error:
        # Never persist raw HTTP responses, credentials, or CLI stderr.
        result={'state':'error','message':str(error) if isinstance(error,RuntimeError) else '조회 응답을 읽지 못했습니다 · 이전 값 유지'}
    if result['state']!='ok':
        result.update(windows=previous.get('windows',[]),checkedAt=time.time())
    write_json(path,result)
    return result
