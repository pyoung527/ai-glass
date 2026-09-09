"""Install local shortcuts and the two reversible usage bridges."""
from pathlib import Path
import json
import os
import shlex
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
launcher = root/'start.sh'
launcher.write_text(f'#!/bin/sh\ncd {shlex.quote(str(root))} || exit 1\nexec /usr/bin/python3 {shlex.quote(str(root/"native.py"))} "$@"\n')
launcher.chmod(0o755)
entry = '\n'.join(['[Desktop Entry]', 'Type=Application', 'Name=AI Glass', 'Comment=Codex · Claude Code · Antigravity 사용량 위젯',
                   f'Exec={json.dumps(str(launcher),ensure_ascii=False)}', f'Icon={root}/assets/widget.png', 'Terminal=false', 'Categories=Utility;', ''])
desktop = Path(subprocess.check_output(['xdg-user-dir','DESKTOP'], text=True).strip())
for folder in [Path.home()/'.local/share/applications', desktop]:
    folder.mkdir(parents=True,exist_ok=True)
    target = folder/'ai-glass.desktop'
    # Refuse to overwrite an unrelated shortcut.
    if target.exists() and str(launcher) not in target.read_text():
        sys.exit(f'기존 파일을 보존했습니다: {target}')
    target.write_text(entry)
    target.chmod(0o755)
# Match Gtk.Application ID so GNOME can identify notification ownership.
(Path.home()/'.local/share/applications/local.ai.glass.desktop').write_text(entry+'NoDisplay=true\n')
subprocess.run(['gio','set',str(desktop/'ai-glass.desktop'),'metadata::trusted','true'], check=False, capture_output=True)
for flags in [[], ['--antigravity']]:
    subprocess.run([sys.executable,str(root/'scripts/statusline_bridge.py'),*flags,'--install'],check=True)
print('AI Glass 바로가기를 바탕화면과 앱 목록에 설치했습니다.')
