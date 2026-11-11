# PyInstaller spec for the installed program; tools\build.ps1 runs it from the build environment.
# One folder, two programs sharing it: ChatMonthly.exe (the window) and mr.exe (the command line the window runs jobs with).
from pathlib import Path

REPO = Path(SPECPATH).parent
ICON = str(REPO / 'src' / 'mr' / 'ui' / 'icon.ico')
# prompts/ and spec/ sit at the bundle root, which is mr.ROOT when frozen; the page is read from mr/ui beside the modules.
# Of spec/ only the reading guide is read at run time; the rest of that folder is local notes and never ships.
DATAS = [(str(REPO / 'src' / 'mr' / 'ui'), 'mr/ui'), (str(REPO / 'prompts'), 'prompts'),
         (str(REPO / 'spec' / '阅读与判断准则.md'), 'spec')]
# pywebview's other back ends are never used on Windows.
EXCLUDES = ['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'qtpy', 'gi', 'cefpython3']
# UTF-8 mode: logs and output files are UTF-8 whatever the system code page is.
UTF8 = [('X utf8_mode=1', None, 'OPTION')]

window = Analysis([str(Path(SPECPATH) / 'window.py')], pathex=[str(REPO / 'src')], datas=DATAS, excludes=EXCLUDES)
cli = Analysis([str(Path(SPECPATH) / 'cli.py')], pathex=[str(REPO / 'src')], excludes=EXCLUDES)

window_exe = EXE(PYZ(window.pure), window.scripts, UTF8, exclude_binaries=True, name='ChatMonthly', icon=ICON,
                 console=False, upx=False)
# Unbuffered, so a job's log shows progress as it happens.
cli_exe = EXE(PYZ(cli.pure), cli.scripts, UTF8 + [('u', None, 'OPTION')], exclude_binaries=True, name='mr', icon=ICON,
              console=True, upx=False)

COLLECT(window_exe, window.binaries, window.datas, cli_exe, cli.binaries, cli.datas, name='ChatMonthly', upx=False)
