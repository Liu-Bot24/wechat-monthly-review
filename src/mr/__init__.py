"""Monthly review of the WeChat archive."""
import sys
from pathlib import Path

__version__ = '1.3.0'

# FROZEN: running from the installed program (PyInstaller) rather than from the repository.
# The program folder has ChatMonthly.exe at the top and everything else in 程序\: mr.exe, prompts/, spec/ and the
# runtime. ROOT holds prompts/ and spec/: the repository, or 程序\. PROGRAM is the program folder, shown as its location.
FROZEN = getattr(sys, 'frozen', False)
PROGRAM_DIR = '程序'


def app_root():
    """The folder ChatMonthly.exe is in, whichever of the two programs is running (mr.exe is in its 程序\\)."""
    exe = Path(sys.executable).resolve()
    return exe.parent.parent if exe.parent.name == PROGRAM_DIR else exe.parent


ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parents[2]
PROGRAM = app_root() if FROZEN else ROOT
