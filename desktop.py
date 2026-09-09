"""GNOME integration using installed GTK/GSettings and libwnck."""
from pathlib import Path
import os
import shlex
import subprocess
from gi.repository import Gio, GLib
from collector import ROOT

AUTOSTART = Path.home()/'.config/autostart/ai-glass.desktop'
BINDING_PATH = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ai-glass/'
BINDING = '<Super><Alt>g'


def autostart_enabled():
    return AUTOSTART.exists() and f'{ROOT}/start.sh' in AUTOSTART.read_text()


def set_autostart(enabled):
    if AUTOSTART.exists() and not autostart_enabled():
        raise ValueError('기존 AI Glass 자동 시작 파일을 먼저 확인하세요.')
    if enabled:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text('[Desktop Entry]\nType=Application\nName=AI Glass\n'
                             f'Exec="{ROOT}/start.sh"\nIcon={ROOT}/assets/widget.png\nTerminal=false\n')
    elif AUTOSTART.exists():
        AUTOSTART.unlink()


def keyboard_settings():
    source = Gio.SettingsSchemaSource.get_default()
    name = 'org.gnome.settings-daemon.plugins.media-keys'
    if not source or not source.lookup(name, True):
        raise ValueError('GNOME 사용자 단축키를 지원하지 않는 환경입니다.')
    return Gio.Settings.new(name)


def shortcut_enabled():
    return BINDING_PATH in keyboard_settings().get_strv('custom-keybindings')


def set_shortcut(enabled):
    parent = keyboard_settings()
    paths = list(parent.get_strv('custom-keybindings'))
    schema = 'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding'
    settings = Gio.Settings.new_with_path(schema, BINDING_PATH)
    command = shlex.quote(str(ROOT/'start.sh'))+' --toggle'
    if enabled:
        for path in paths:
            other = Gio.Settings.new_with_path(schema, path)
            if path != BINDING_PATH and other.get_string('binding').lower() == BINDING.lower():
                raise ValueError('Super+Alt+G 단축키가 이미 사용 중입니다.')
        if BINDING_PATH in paths and settings.get_string('command') != command:
            raise ValueError('기존 단축키 명령이 있어 덮어쓰지 않았습니다.')
        settings.set_string('name', 'AI Glass 표시 / 숨김')
        settings.set_string('command', command)
        settings.set_string('binding', BINDING)
        if BINDING_PATH not in paths:
            paths.append(BINDING_PATH)
    else:
        paths = [p for p in paths if p != BINDING_PATH]
    if not parent.set_strv('custom-keybindings', paths):
        raise ValueError('단축키 설정을 저장하지 못했습니다.')
    Gio.Settings.sync()


def resume_command(provider, session):
    executable = {'codex':'codex', 'claude':'claude', 'antigravity':'agy'}[provider]
    flag = {'codex':'resume', 'claude':'--resume', 'antigravity':'--conversation'}[provider]
    command = shlex.join([executable, flag, session['id']])
    return (f"cd {shlex.quote(session['cwd'])} && " if session.get('cwd') else '')+command


def resume(provider, session):
    if session.get('cwd') and not Path(session['cwd']).is_dir():
        raise ValueError('프로젝트 폴더가 없습니다. 재개 명령을 복사해 경로를 확인하세요.')
    command = resume_command(provider, session)
    # Keep the terminal visible on command failure instead of silently closing it.
    command += '; result=$?; if [ "$result" -ne 0 ]; then printf "\\n실행 실패. Enter를 누르면 닫습니다."; read answer; fi'
    subprocess.Popen(['x-terminal-emulator', '--', '/bin/bash', '-lc', command], start_new_session=True)


def session_windows(session):
    import gi
    gi.require_version('Wnck', '3.0')
    from gi.repository import Wnck
    candidates = set(session.get('pids', []))
    # Follow exact session processes to their terminal, never guess by a project title.
    for pid in list(candidates):
        for _ in range(12):
            try:
                parent = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
                if parent <= 1 or parent == os.getpid():
                    break
                candidates.add(parent)
                pid = parent
            except (OSError, ValueError, IndexError):
                break
    screen = Wnck.Screen.get_default()
    if screen is None:
        raise ValueError('현재 디스플레이에서 창 이동을 지원하지 않습니다.')
    screen.force_update()
    matches = [w for w in screen.get_windows() if w.get_pid() in candidates and w.get_pid() != os.getpid()]
    return matches


def focus_window(window, timestamp):
    workspace = window.get_workspace()
    if workspace:
        workspace.activate(timestamp)
    window.activate(timestamp)
