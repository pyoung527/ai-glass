"""Read local agent metadata only. Never read authentication files or call model APIs."""
import json
import logging
import math
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from app_paths import ROOT, HOME, DATA, WINDOWS
import agent_runtime
_auth_checked = 0
_claude_logged_in = None


def claude_logged_in():
    global _auth_checked, _claude_logged_in
    if time.time()-_auth_checked < 60:
        return _claude_logged_in
    try:
        result=subprocess.run(agent_runtime.command('claude','auth','status'),capture_output=True,text=True,timeout=4,**agent_runtime.background_options())
        _claude_logged_in=json.loads(result.stdout).get('loggedIn')
    except (OSError,ValueError,subprocess.TimeoutExpired):
        _claude_logged_in=None
    _auth_checked=time.time()
    return _claude_logged_in


def read_json(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    if isinstance(default, dict) and not isinstance(value, dict):
        raise ValueError(f'Expected object in {Path(path).name}')
    return value


def records(path, tail=False):
    try:
        with open(path, 'rb') as f:
            # ponytail: bounded log reads; add incremental offsets if large histories need deeper scans.
            if tail:
                size = f.seek(0, 2)
                f.seek(max(0, size - 1024 * 1024))
                if size > 1024 * 1024:
                    f.readline()
            content = f.read(1024 * 1024).decode('utf-8', errors='replace')
        for line in content.splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                continue  # Agents may be halfway through appending a record.
    except OSError:
        return


def windows(raw, stamp, provider, now=None):
    now = time.time() if now is None else now
    if not isinstance(raw, dict):
        return []
    stamp = stamp if isinstance(stamp, (int, float)) and math.isfinite(stamp) else 0
    result = []
    for key, value in (raw or {}).items():
        if not isinstance(value, dict):
            continue
        used = value.get('used_percent', value.get('used_percentage'))
        if not isinstance(used, (int, float)) or isinstance(used, bool) or not 0 <= used <= 100:
            continue
        reset = value.get('resets_at')
        if isinstance(reset, str):
            try:
                reset = datetime.fromisoformat(reset.replace('Z', '+00:00')).timestamp()
            except ValueError:
                reset = None
        if isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset):
            reset = None
        minutes = value.get('window_minutes')
        label = {'five_hour': '5시간', 'seven_day': '주간', 'gemini-weekly':'Gemini 주간', '3p-weekly':'Claude/GPT 주간'}.get(key)
        label = label or ('주간' if minutes == 10080 else '5시간' if minutes == 300 else f'{minutes}분' if minutes else key)
        expired = isinstance(reset, (int, float)) and reset <= now
        result.append(dict(key=key, label=label, remaining=None if expired else round(100-used, 1), resetsAt=reset,
                           observedAt=stamp, stale=expired or now-stamp > 900 or stamp > now+60, source=provider))
    return result


def live_sessions():
    """Exact UUID argv or open transcript fd matches; a shared cwd isn't proof of a session."""
    if WINDOWS:
        return agent_runtime.windows_live_sessions()
    live_ids, live_paths, owners = set(), set(), {}
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = (proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
            executable = Path(args[0]).name
            is_agent = executable in ('codex', 'claude', 'agy', 'language_server', 'language_server_linux_x64') or (
                executable in ('node', 'bun') and any('/codex/' in a or '/gemini-cli/' in a or '/claude-code/' in a for a in args[1:3]))
            if not is_agent:
                continue
            for arg in args[1:]:
                sid = arg.split('=', 1)[-1]
                if len(sid) == 36 and sid.count('-') == 4:
                    live_ids.add(sid)
                    owners.setdefault(sid, set()).add(int(proc.name))
            for fd in (proc/'fd').iterdir():
                try:
                    target = os.readlink(fd)
                    if target.endswith(('.jsonl', '.json', '.db')):
                        live_paths.add(target)
                        owners.setdefault(target, set()).add(int(proc.name))
                    elif target.endswith(('.db-wal', '.db-shm')):
                        live_paths.add(target[:-4])
                        owners.setdefault(target[:-4], set()).add(int(proc.name))
                except OSError:
                    pass
        except (OSError, IndexError):
            continue
    return live_ids, live_paths, owners


def process_ids(live, sid, *paths):
    owners = live[2] if len(live) > 2 else {}
    return sorted(set().union(*(owners.get(str(key), set()) for key in (sid, *paths))))


def session_status(path, sid, updated, live, busy=False):
    if sid in live[0] or str(path) in live[1]:
        return 'working' if busy else 'open'
    return 'recent' if time.time()-updated < 300 else 'saved'


def codex(live):
    home = Path(os.getenv('CODEX_HOME', HOME/'.codex'))
    dbs = sorted(home.glob('state_*.sqlite'), key=lambda p:p.stat().st_mtime, reverse=True)
    sessions, snapshots = [], []
    if not dbs:
        return dict(id='codex', sessions=[], windows=[], message='Codex에서 첫 세션을 시작하세요.')
    with sqlite3.connect(dbs[0].as_uri()+'?mode=ro', uri=True, timeout=1) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT id,title,cwd,rollout_path,updated_at FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT 60').fetchall()
    for row in rows:
        path = row['rollout_path']
        busy = False
        for record in records(path, tail=True):
            payload = record.get('payload', {})
            if record.get('type') != 'event_msg':
                continue
            event = payload.get('type')
            if event in ('task_started', 'turn_started'):
                busy = True
            elif event in ('task_complete', 'task_completed', 'turn_aborted', 'turn_complete'):
                busy = False
            rates = payload.get('rate_limits')
            if isinstance(rates, dict) and rates.get('limit_id') in (None, 'codex'):
                from datetime import datetime
                try:
                    stamp = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).timestamp()
                    parsed = windows(rates, stamp, 'Codex 기록')
                    if parsed:
                        snapshots.append((stamp, parsed))
                except (ValueError, KeyError):
                    pass
        updated = row['updated_at']
        sessions.append(dict(id=row['id'], title=row['title'] or Path(row['cwd']).name, cwd=row['cwd'], pids=process_ids(live, row['id'], path),
                             updatedAt=updated, status=session_status(path, row['id'], updated, live, busy)))
    latest = max(snapshots, key=lambda item:item[0])[1] if snapshots else []
    return dict(id='codex', sessions=sessions, windows=latest, message='Codex 로컬 기록 · ChatGPT 웹 한도와 별도')


def connection_status(provider, home):
    if provider=='claude' and claude_logged_in() is False:
        return {'connectionLabel':'로그인 필요','message':'Claude 구독 로그인이 만료됐거나 연결되지 않았습니다. 아래 구독 로그인 버튼으로 로그인한 뒤 /usage를 확인하세요.','action':'login'}
    config=read_json(home/'settings.json',{}).get('statusLine',{})
    expected=str(ROOT/'scripts/statusline_bridge.py')
    if not isinstance(config,dict) or expected not in config.get('command','') or config.get('enabled') is False:
        return {'connectionLabel':'연결 설정 필요','message':'상태 표시줄 연결이 꺼져 있습니다. 연결 스크립트를 다시 설치하세요.'}
    status=read_json(DATA/f'{provider}-connection.json',{})
    if not status:
        command='claude' if provider=='claude' else 'agy'
        return {'connectionLabel':'CLI 갱신 필요','message':f'연결 설정은 완료됐지만 아직 수신 기록이 없습니다. 터미널에서 {command}를 실행하고 /usage를 확인하세요. 대화형 CLI가 한도를 보내면 표시됩니다.'}
    return {'connectionLabel':'한도 정보 없음','message':'상태 표시줄은 호출됐지만 표시할 한도가 없습니다. CLI의 /usage와 버전·로그인 방식을 확인하세요.'}


def claude(live):
    home = Path(os.getenv('CLAUDE_CONFIG_DIR', HOME/'.claude'))
    files = sorted((home/'projects').glob('*/*.jsonl'), key=lambda p:p.stat().st_mtime, reverse=True)[:60]
    sessions = []
    for path in files:
        head = list(records(path))
        tail = list(records(path, tail=True))
        cwd = next((r.get('cwd') for r in head if r.get('cwd')), '')
        title = next((r.get('aiTitle') or r.get('customTitle') for r in reversed(tail) if r.get('aiTitle') or r.get('customTitle')), None)
        updated = path.stat().st_mtime
        sessions.append(dict(id=path.stem, title=title or Path(cwd).name or 'Claude 세션', cwd=cwd, pids=process_ids(live, path.stem, path),
                             updatedAt=updated, status=session_status(path, path.stem, updated, live)))
    cache = read_json(DATA/'claude-usage.json', {})
    usage = windows(cache.get('rate_limits'), cache.get('observedAt', 0), 'Claude 상태 표시줄')
    info = connection_status('claude',home) if not usage or all(w['stale'] for w in usage) else {}
    if usage and info.get('connectionLabel') not in ('로그인 필요','연결 설정 필요'):
        info = {'message':'Claude 상태 표시줄에서 확인한 계정 한도'}
    return dict(id='claude', sessions=sessions, windows=usage, **info)


def antigravity(live):
    home = Path(os.getenv('ANTIGRAVITY_CONFIG_DIR', HOME/'.gemini/antigravity-cli'))
    files = sorted((home/'conversations').glob('*.db'), key=lambda p:p.stat().st_mtime, reverse=True)[:60]
    recent = read_json(home/'cache/last_conversations.json', {})
    workspaces = {sid:cwd for cwd,sid in recent.items()}
    sessions = []
    for path in files:
        sid = path.stem
        transcript = home/'brain'/sid/'.system_generated/logs/transcript.jsonl'
        prompt = next((r.get('content') for r in records(transcript) if r.get('type') == 'USER_INPUT' and isinstance(r.get('content'), str)), '')
        cwd = workspaces.get(sid, '')
        updated = max(p.stat().st_mtime for p in [path, Path(str(path)+'-wal'), transcript] if p.exists())
        status = session_status(path, sid, updated, live)
        if str(transcript) in live[1]:
            status = 'open'
        sessions.append(dict(id=sid, title=' '.join(prompt.split())[:100] or Path(cwd).name or f'Antigravity · {sid[:8]}',
                             cwd=cwd, pids=process_ids(live, sid, path, transcript), updatedAt=updated, status=status))
    sessions.sort(key=lambda s:s['updatedAt'], reverse=True)
    cache = read_json(DATA/'antigravity-usage.json', {})
    usage = windows(cache.get('rate_limits'), cache.get('observedAt', 0), 'Antigravity 상태 표시줄')
    return dict(id='antigravity', sessions=sessions, windows=usage, **({'message':'Antigravity 모델별 잔여 한도'} if usage else connection_status('antigravity',home)))


def apply_remote(provider):
    try:
        cache=read_json(DATA/f"{provider['id']}-remote.json",{})
        if not cache: return
        remote=cache.get('windows',[])
        stamp=max((w['observedAt'] for w in remote),default=0)
        local_stamp=max((w['observedAt'] for w in provider['windows']),default=0)
        if stamp>local_stamp:
            provider['windows']=[dict(w, remaining=None if w.get('resetsAt') and w['resetsAt']<=time.time() else w['remaining'],
                                      stale=time.time()-w['observedAt']>900 or bool(w.get('resetsAt') and w['resetsAt']<=time.time()) or cache['state']!='ok') for w in remote]
        if cache['checkedAt']>=local_stamp:
            provider['remoteState']=cache['state']
            provider['remoteMessage']=cache['message']
            provider['message']=cache['message']
            if cache['state']=='login':
                provider['connectionLabel']='로그인 필요'
                if provider['id']=='claude': provider['action']='login'
            elif cache['state']=='ok' and stamp>=local_stamp:
                provider.pop('connectionLabel',None)
                provider.pop('action',None)
    except (OSError,ValueError,TypeError,KeyError):
        logging.warning('Invalid remote usage cache for %s',provider['id'])


def collect(previous=None):
    live = live_sessions()
    providers = []
    previous = {p['id']: p for p in (previous or {}).get('providers', [])}
    for name, reader in [('codex', codex), ('claude', claude), ('antigravity', antigravity)]:
        try:
            provider = reader(live)
        except Exception as error:
            # Isolate provider schema changes and preserve the other providers.
            logging.exception('AI Glass provider %s failed', name)
            old = previous.get(name, {})
            provider = dict(id=name, sessions=[], windows=[dict(w, stale=True) for w in old.get('windows', [])],
                            message=f'기록 읽기 오류 ({type(error).__name__}). 마지막 사용량을 보존했습니다. 연결 점검을 실행하세요.',
                            connectionLabel='읽기 오류', error=True)
        apply_remote(provider)
        providers.append(provider)
    return dict(providers=providers, checkedAt=time.time())


if __name__ == '__main__':
    print(json.dumps(collect(), ensure_ascii=False))
