"""Windows desktop integration; no WSL or administrator privileges required."""
import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import agent_runtime


def powershell_literal(value):
    return "'"+str(value).replace("'", "''")+"'"


def terminal_command(provider, session=None):
    args=[]
    if session:
        args=[{'codex':'resume','claude':'--resume','antigravity':'--conversation'}[provider],session['id']]
    argv=agent_runtime.command({'codex':'codex','claude':'claude','antigravity':'agy'}[provider],*args)
    script=''
    if session and session.get('cwd'):
        script='Set-Location -LiteralPath '+powershell_literal(session['cwd'])+' -ErrorAction Stop; '
    script+='& '+' '.join(map(powershell_literal,argv))
    return ['powershell.exe','-NoLogo','-NoProfile','-NoExit','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode('ascii')]


def open_terminal(provider, session=None):
    if session and session.get('cwd') and not Path(session['cwd']).is_dir():
        raise ValueError('프로젝트 폴더를 찾지 못했습니다.')
    subprocess.Popen(terminal_command(provider,session),creationflags=subprocess.CREATE_NEW_CONSOLE)


def focus_session(session):
    import psutil
    ids=set(session.get('pids',[]))
    if not ids: return False
    for pid in list(ids):
        try: ids.update(p.pid for p in psutil.Process(pid).parents() if p.pid!=os.getpid())
        except psutil.Error: pass
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
    user.SetForegroundWindow.argtypes=[wintypes.HWND]
    found=[]
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @callback
    def visit(hwnd,_):
        pid=wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value in ids and user.IsWindowVisible(hwnd): found.append(hwnd)
        return True
    user.EnumWindows(visit,0)
    if len(found)!=1: return False  # A shared terminal isn't proof of the selected tab.
    user.ShowWindow(found[0],9)
    return bool(user.SetForegroundWindow(found[0]))


def startup_command():
    args=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('windows_app.py'))]
    return subprocess.list2cmdline(args)


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            return winreg.QueryValueEx(key,'AI Glass')[0]==startup_command()
    except FileNotFoundError: return False


def set_autostart(enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        try: current=winreg.QueryValueEx(key,'AI Glass')[0]
        except FileNotFoundError: current=None
        if current and current!=startup_command(): raise ValueError('다른 AI Glass 자동 시작 설정이 있어 보존했습니다.')
        if enabled: winreg.SetValueEx(key,'AI Glass',0,winreg.REG_SZ,startup_command())
        elif current: winreg.DeleteValue(key,'AI Glass')
