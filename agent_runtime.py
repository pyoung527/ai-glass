"""Native agent execution without interpolating session metadata into a shell."""
import os
from pathlib import Path
import shutil
import subprocess
from app_paths import HOME, WINDOWS


def command(name, *args):
    found = shutil.which(name)
    if not found:
        folders = [HOME/'.local/bin', HOME/'AppData/Roaming/npm']
        for folder in folders:
            for suffix in (('.exe', '.cmd', '') if WINDOWS else ('',)):
                candidate = folder/(name+suffix)
                if candidate.is_file():
                    found = str(candidate); break
            if found: break
    if not found:
        raise RuntimeError(f'{name} 실행 파일이 없습니다. CLI 설치 후 위젯을 다시 실행하세요.')
    if WINDOWS and Path(found).suffix.lower() in ('.cmd', '.bat'):
        # npm Codex shims need node; avoid cmd.exe expansion of arbitrary metadata.
        script = Path(found).parent/'node_modules/@openai/codex/bin/codex.js'
        node = shutil.which('node')
        if name != 'codex' or not script.is_file() or not node:
            raise RuntimeError(f'{name} 네이티브 실행 파일을 설치하세요.')
        return [node, str(script), *args]
    return [found, *args]


def background_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if WINDOWS else {}


def stop(process):
    if process.poll() is not None: return
    if WINDOWS:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, timeout=5, **background_options())
    else:
        process.terminate()
    try: process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=2)


def windows_live_sessions():
    import psutil
    ids, paths, owners = set(), set(), {}
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            args = process.info['cmdline'] or []
            name = (process.info['name'] or '').lower()
            agent = name in ('codex.exe', 'claude.exe', 'agy.exe', 'language_server_windows_x64.exe')
            agent |= name in ('node.exe', 'bun.exe') and any('/codex/' in a.replace('\\','/') or '/claude-code/' in a.replace('\\','/') for a in args[1:3])
            if not agent: continue
            for arg in args[1:]:
                sid = arg.split('=', 1)[-1]
                if len(sid)==36 and sid.count('-')==4:
                    ids.add(sid); owners.setdefault(sid,set()).add(process.pid)
            for file in process.open_files():
                path = file.path
                if path.endswith(('.db-wal','.db-shm')): path=path[:-4]
                if path.endswith(('.jsonl','.json','.db')):
                    paths.add(path); owners.setdefault(path,set()).add(process.pid)
        except (psutil.Error, OSError):
            continue
    return ids, paths, owners
