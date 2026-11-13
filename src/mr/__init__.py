"""Monthly review of the WeChat archive."""
import sys
from pathlib import Path

__version__ = '1.1.0'

# FROZEN: running from the installed program (PyInstaller) rather than from the repository.
# ROOT holds prompts/ and spec/: the repository, or the bundle folder the installer puts next to the executables.
# PROGRAM is the folder to show as the program's location.
FROZEN = getattr(sys, 'frozen', False)
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parents[2]
PROGRAM = Path(sys.executable).parent if FROZEN else ROOT
