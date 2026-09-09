"""Read-only application resources and writable per-user state."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
WINDOWS = sys.platform == 'win32'
# Source checkouts retain their existing data; installed copies use user storage.
_default = (Path(os.getenv('LOCALAPPDATA', HOME/'AppData/Local'))/'AI Glass' if WINDOWS
            else Path(os.getenv('XDG_DATA_HOME', HOME/'.local/share'))/'ai-glass')
if not getattr(sys, 'frozen', False) and (ROOT/'.git').exists():
    _default = ROOT/'data'
DATA = Path(os.getenv('AI_GLASS_DATA_DIR', _default))
