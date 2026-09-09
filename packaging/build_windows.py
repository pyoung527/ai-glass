"""Build on Windows with Python 3.12 and requirements-windows.txt."""
import os
import shutil
import importlib.metadata
from pathlib import Path
import subprocess
import sys
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
Path('build').mkdir(exist_ok=True)
with Image.open('assets/widget.png') as icon:
    icon.save('build/widget.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
    '--name','AI-Glass','--icon','build/widget.ico','--add-data','assets:assets',
    '--add-data','LICENSE:.','--add-data','THIRD_PARTY_NOTICES.md:.',
    '--collect-all','winpty','--hidden-import','psutil','windows_app.py'],check=True)
licenses=Path('dist/AI-Glass/licenses')
shutil.copytree('packaging/licenses',licenses,dirs_exist_ok=True)
for package in ['psutil','pywinpty','PyInstaller','PySide6','PySide6_Essentials','shiboken6']:
    dist=importlib.metadata.distribution(package)
    for file in dist.files or []:
        if 'license' in str(file).lower() or 'copying' in str(file).lower():
            source=Path(dist.locate_file(file))
            if source.is_file():
                target=licenses/package/str(file);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
os.environ['AI_GLASS_VERSION']=(ROOT/'VERSION').read_text().strip()
compiler=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Inno Setup 6/ISCC.exe'
subprocess.run([str(compiler),str(ROOT/'packaging/windows.iss')],check=True)
