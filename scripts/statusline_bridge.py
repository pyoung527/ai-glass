"""Capture documented Claude/Antigravity quotas, preserving existing status lines."""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app_paths import ROOT, DATA
PROVIDER = 'antigravity' if '--antigravity' in sys.argv else 'claude'
NAME = 'Antigravity' if PROVIDER == 'antigravity' else 'Claude'
CONFIG = Path(os.getenv('ANTIGRAVITY_CONFIG_DIR', Path.home()/'.gemini/antigravity-cli')) if PROVIDER == 'antigravity' else Path(os.getenv('CLAUDE_CONFIG_DIR', Path.home()/'.claude'))
SETTINGS = CONFIG/'settings.json'
BACKUP = DATA/f'{PROVIDER}-statusline-backup.json'
_args=[sys.executable,str(Path(__file__).resolve())]+(['--antigravity'] if PROVIDER=='antigravity' else [])
COMMAND = subprocess.list2cmdline(_args) if os.name=='nt' else shlex.join(_args)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.ai-glass-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install(remove=False):
    settings = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    current = settings.get('statusLine')
    connected = isinstance(current, dict) and current.get('command') == COMMAND
    if remove:
        if not connected:
            print('현재 상태 표시줄이 AI Glass 연결이 아니므로 변경하지 않았습니다.')
            return
        previous = json.loads(BACKUP.read_text(encoding="utf-8"))
        if previous is None:
            settings.pop('statusLine', None)
        else:
            settings['statusLine'] = previous
    else:
        if connected:
            print(f'{NAME} 사용량이 이미 연결되어 있습니다.')
            return
        write_json(BACKUP, current)
        settings['statusLine'] = {**(current or {}), 'type':'command', 'command':COMMAND}
        if PROVIDER == 'antigravity':
            settings['statusLine']['enabled'] = True
            if current is None:
                settings['statusLine']['stack_with_default'] = True
    write_json(SETTINGS, settings)
    print('기존 상태 표시줄을 복원했습니다.' if remove else f'{NAME} 연결 완료. 다음 상태 갱신부터 사용량이 표시됩니다.')


def capture():
    raw = sys.stdin.buffer.read(2*1024*1024)
    try:
        payload = json.loads(raw)
        write_json(DATA/f'{PROVIDER}-connection.json', {
            'lastCalled':time.time(), 'version':str(payload.get('version',''))[:40],
            'hasQuota':bool(payload.get('rate_limits') if PROVIDER=='claude' else payload.get('quota'))
        })
        rates = payload.get('rate_limits') if PROVIDER == 'claude' else None
        if PROVIDER == 'antigravity' and isinstance(payload.get('quota'), dict):
            rates = {}
            for key, value in payload['quota'].items():
                if not isinstance(value, dict):
                    continue
                fraction = value.get('remaining_fraction')
                reset = value.get('reset_time')
                if isinstance(reset, str):
                    try:
                        reset = datetime.fromisoformat(reset.replace('Z', '+00:00')).timestamp()
                    except ValueError:
                        reset = None
                if isinstance(fraction, (int, float)) and not isinstance(fraction, bool) and 0 <= fraction <= 1:
                    rates[key] = {'used_percentage':round((1-fraction)*100, 4), 'resets_at':reset}
        if isinstance(rates, dict):
            clean = {key: {k:value.get(k) for k in ('used_percentage','resets_at')}
                     for key,value in rates.items() if (PROVIDER == 'antigravity' or key in ('five_hour','seven_day')) and isinstance(value, dict) and isinstance(value.get('used_percentage'), (int,float)) and not isinstance(value.get('used_percentage'), bool) and math.isfinite(value['used_percentage']) and 0 <= value['used_percentage'] <= 100}
            if clean:
                write_json(DATA/f'{PROVIDER}-usage.json', {'rate_limits':clean, 'observedAt':time.time()})
    except (ValueError, OSError, AttributeError):
        pass  # A failed widget update must not break Claude's status line.
    try:
        previous = json.loads(BACKUP.read_text(encoding="utf-8"))
        if isinstance(previous, dict) and previous.get('type') == 'command' and previous.get('command') != COMMAND:
            subprocess.run(previous['command'], input=raw, shell=True, timeout=5, check=False)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


if __name__ == '__main__':
    if '--install' in sys.argv or '--uninstall' in sys.argv:
        install('--uninstall' in sys.argv)
    else:
        capture()
