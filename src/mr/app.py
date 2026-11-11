"""Start the window: one instance, a local server on a free port, and a WebView2 window on it.

  pythonw -m mr.app            the program (what the shortcut runs)
  python -m mr.app --serve     the server only, for development: http://127.0.0.1:8765/
"""
import argparse, ctypes, ctypes.wintypes, json, logging, logging.handlers, socket, sys, threading, time
from pathlib import Path
from . import config

TITLE = '消息月报'
MUTEX = 'Local\\ChatMonthlyWindow'

def _log():
    logs = config.HOME / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(logs / 'window.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    if sys.stderr is None or sys.executable.lower().endswith('pythonw.exe'):
        # pythonw has no console; keep stray prints and tracebacks in the log instead of losing them.
        stream = open(logs / 'window.out.log', 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = stream

def _free_port(preferred=None):
    for port in ([preferred] if preferred else []) + [0]:
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
                return s.getsockname()[1]
            except OSError:
                continue

def _already_running():
    """True when another window holds the mutex; that window is brought to the front."""
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.CreateMutexW(None, False, MUTEX)
    if k32.GetLastError() != 183:          # ERROR_ALREADY_EXISTS
        return False
    hwnd = u32.FindWindowW(None, TITLE)
    if hwnd:
        u32.ShowWindow(hwnd, 9)            # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
    return True

def _set_icon():
    """Give the window the program's icon; pythonw's own icon would show in the title bar and taskbar."""
    u32 = ctypes.windll.user32
    u32.LoadImageW.restype = ctypes.c_void_p
    u32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
    hwnd = u32.FindWindowW(None, TITLE)
    ico = str(Path(__file__).resolve().parent / 'ui' / 'icon.ico')
    for which, size in ((1, 32), (0, 16)):            # ICON_BIG, ICON_SMALL
        h = u32.LoadImageW(None, ico, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if hwnd and h:
            u32.SendMessageW(hwnd, 0x80, which, h)    # WM_SETICON

class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [('length', ctypes.c_uint), ('flags', ctypes.c_uint), ('showCmd', ctypes.c_uint),
                ('ptMinPosition', ctypes.wintypes.POINT), ('ptMaxPosition', ctypes.wintypes.POINT), ('rcNormalPosition', ctypes.wintypes.RECT)]

def _placement():
    """The window's normal (not maximized) bounds in DPI-independent pixels, which is what create_window takes.
    window.width and friends are physical pixels; saving those made the window 1.5x larger on every start at 150%."""
    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, TITLE)
    if not hwnd:
        return None
    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(wp)
    u32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    s = u32.GetDpiForWindow(hwnd) / 96 or 1
    r = wp.rcNormalPosition
    return {'x': round(r.left / s), 'y': round(r.top / s), 'width': round((r.right - r.left) / s),
            'height': round((r.bottom - r.top) / s), 'maximized': wp.showCmd == 3}   # SW_SHOWMAXIMIZED

def _fit(geo):
    """Saved bounds, shrunk and moved to fit the primary screen's work area (also repairs sizes saved by 1.0)."""
    u32 = ctypes.windll.user32
    area = ctypes.wintypes.RECT()
    u32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)      # SPI_GETWORKAREA, physical pixels
    s = u32.GetDpiForSystem() / 96 or 1
    aw, ah = (area.right - area.left) / s, (area.bottom - area.top) / s
    w, h = min(geo.get('width', 1440), aw), min(geo.get('height', 900), ah)
    x, y = geo.get('x'), geo.get('y')
    if x is not None and y is not None:
        # Keep the title bar reachable; a window saved on a monitor that is gone comes back to the primary one.
        vx, vy = u32.GetSystemMetrics(76) / s, u32.GetSystemMetrics(77) / s      # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        vw, vh = u32.GetSystemMetrics(78) / s, u32.GetSystemMetrics(79) / s      # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
        if not (vx <= x <= vx + vw - 100 and vy <= y <= vy + vh - 60):
            x = y = None
        elif x + w > vx + vw or y + h > vy + vh:
            x, y = max(vx, vx + vw - w), max(vy, vy + vh - h)
    return x, y, int(w), int(h)

def _serve(app, port):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError('本机服务没有启动')


def _on_ui(window, fn):
    """Run fn(WebView2 control) on the window's UI thread and return its result; None when there is no WebView2."""
    form = getattr(window, 'native', None)
    web = getattr(getattr(form, 'browser', None), 'webview', None)
    if web is None:
        return None
    from System import Func, Object
    return form.Invoke(Func[Object](lambda: fn(web)))


class Bridge:
    """Functions the page may call through window.pywebview.api."""

    def __init__(self):
        self._window = None      # private: pywebview exposes every public attribute to the page and cannot walk a Window

    def pick_folder(self, initial=''):
        import webview
        r = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=initial or '')
        return r[0] if r else None

    def get_zoom(self):
        v = _on_ui(self._window, lambda w: w.ZoomFactor)
        return None if v is None else round(float(str(v)), 3)   # a boxed System.Double

    def set_zoom(self, factor):
        factor = min(2.0, max(0.5, float(factor)))
        _on_ui(self._window, lambda w: setattr(w, 'ZoomFactor', factor))
        return factor

    def save_text(self, name, text):
        """Ask where to save, then write text as UTF-8 with a BOM so Excel reads Chinese correctly."""
        import webview
        r = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename=name, file_types=('CSV 文件 (*.csv)', '所有文件 (*.*)'))
        if not r:
            return None
        path = r if isinstance(r, str) else r[0]
        Path(path).write_text(text, encoding='utf-8-sig')
        return path

    def pick_save(self, name, kind=''):
        """Where to save a file the server writes; kind 'zip' limits the dialog to .zip."""
        import webview
        types = ('配置文件 (*.zip)', '所有文件 (*.*)') if kind == 'zip' else ('所有文件 (*.*)',)
        r = self._window.create_file_dialog(webview.SAVE_DIALOG, directory=str(Path(name).parent), save_filename=Path(name).name, file_types=types)
        if not r:
            return None
        return r if isinstance(r, str) else r[0]

    def pick_open(self, kind=''):
        import webview
        types = {'zip': ('配置文件 (*.zip)', '所有文件 (*.*)')}.get(kind, ('所有文件 (*.*)',))
        r = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=types)
        return r[0] if r else None

    def pick_file(self, initial=''):
        import webview
        r = self._window.create_file_dialog(webview.OPEN_DIALOG, directory=str(Path(initial).parent) if initial else '',
                                           file_types=('程序 (*.exe)', '所有文件 (*.*)'))
        return r[0] if r else None


def main(argv=None):
    p = argparse.ArgumentParser(prog='mr.app')
    p.add_argument('--serve', action='store_true', help='run the server only (development)')
    p.add_argument('--port', type=int, default=8765)
    args = p.parse_args(argv)
    from . import server
    if args.serve:
        import uvicorn
        app = server.make()
        print(f'http://127.0.0.1:{args.port}/', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
        return 0

    _log()
    if _already_running():
        return 0
    # Its own taskbar group, so the window is not stacked with other Python programs.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ChatMonthly.App')
    import webview
    app = server.make()
    port = _free_port()
    srv = _serve(app, port)
    logging.info('server on %s', port)
    state_path = config.HOME / 'window.json'
    try:
        geo = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        geo = {}
    bridge = Bridge()
    x, y, w, h = _fit(geo)
    window = webview.create_window(TITLE, f'http://127.0.0.1:{port}/', js_api=bridge, width=w, height=h,
                                   x=x, y=y, min_size=(1100, 720), maximized=geo.get('maximized', False),
                                   background_color='#f6f7f9', text_select=True, zoomable=True)
    bridge._window = window

    def remember(*_):
        try:
            state = _placement() or {}
            if state and first:
                # The created window comes out a few pixels smaller than asked; add that back so it does not shrink each start.
                state['width'] += w - first['width']
                state['height'] += h - first['height']
            state['zoom'] = bridge.get_zoom() or geo.get('zoom', 1.0)
            state_path.write_text(json.dumps(state), encoding='utf-8')
        except Exception:
            logging.exception('saving window state')

    first = None

    def shown(*_):
        nonlocal first
        _set_icon()
        first = _placement()     # normal bounds, also while maximized
        if geo.get('zoom', 1.0) != 1.0:
            try:
                bridge.set_zoom(geo['zoom'])
            except Exception:
                logging.exception('restoring zoom')

    def closing():
        remember()
        if server.jobs and server.jobs.data.get('current'):
            return window.create_confirmation_dialog(TITLE, '有任务正在运行。关闭窗口后它会在后台继续运行；排队中的任务会在下次打开窗口时继续。\n\n确定关闭窗口吗？')
        return True

    window.events.closing += closing
    window.events.shown += shown
    webview.start(private_mode=False, storage_path=str(config.HOME / 'webview'))
    server.jobs.close()
    srv.should_exit = True
    return 0

if __name__ == '__main__':
    sys.exit(main())
