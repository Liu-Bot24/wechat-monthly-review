"""mr.exe: the installed program's command line. The window runs its jobs with it; it takes the same commands as python -m mr."""
import ctypes, sys
from mr.__main__ import main

# Held until the process ends, so the installer can tell that a job is still running (installer.iss AppMutex).
ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\ChatMonthlyJob')
sys.exit(main())
