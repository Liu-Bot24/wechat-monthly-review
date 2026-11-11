"""Start `mr ...` from the repository, from any folder and without PYTHONPATH: python tools\\run_mr.py <command> ...
The installed program has mr.exe for this."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from mr.__main__ import main

sys.exit(main())
