"""Build an explicit allowlist .deb; never copy the working directory wholesale."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION').read_text().strip()
FILES=['native.py','collector.py','remote_usage.py','desktop.py','usage_state.py','app_paths.py','agent_runtime.py','start.sh','scripts/statusline_bridge.py']
with tempfile.TemporaryDirectory() as tmp:
    stage=Path(tmp);app=stage/'usr/share/ai-glass';app.mkdir(parents=True)
    for name in FILES:
        target=app/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
    shutil.copytree(ROOT/'assets',app/'assets')
    app.joinpath('start.sh').chmod(0o755)
    launch=stage/'usr/bin/ai-glass';launch.parent.mkdir(parents=True)
    launch.write_text('#!/bin/sh\nexec /usr/share/ai-glass/start.sh "$@"\n');launch.chmod(0o755)
    entries=stage/'usr/share/applications';entries.mkdir(parents=True)
    (entries/'local.ai.glass.desktop').write_text('[Desktop Entry]\nType=Application\nName=AI Glass\nComment=Local AI agent quota widget\nExec=ai-glass\nIcon=ai-glass\nTerminal=false\nCategories=Utility;\nStartupWMClass=local.ai.glass\n')
    icons=stage/'usr/share/icons/hicolor/256x256/apps';icons.mkdir(parents=True)
    shutil.copy2(ROOT/'assets/widget.png',icons/'ai-glass.png')
    docs=stage/'usr/share/doc/ai-glass';docs.mkdir(parents=True)
    for name in ['LICENSE','README.md','THIRD_PARTY_NOTICES.md']:shutil.copy2(ROOT/name,docs/name)
    control=stage/'DEBIAN';control.mkdir()
    (control/'control').write_text(f'Package: ai-glass\nVersion: {VERSION}\nArchitecture: amd64\nMaintainer: AI Glass contributors\nSection: utils\nPriority: optional\nDepends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-wnck-3.0, gir1.2-gdkpixbuf-2.0, librsvg2-common, dbus-bin, xterm\nDescription: Native glass-style AI coding agent quota widget\n Shows local sessions and account quota for Codex, Claude Code and Antigravity.\n')
    for path in stage.rglob('*'):
        path.chmod(0o755 if path.is_dir() or path.name in ('start.sh','ai-glass') else 0o644)
    stage.chmod(0o755)
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    subprocess.run(['dpkg-deb','--root-owner-group','--build',str(stage),str(output/f'ai-glass_{VERSION}_amd64.deb')],check=True)
