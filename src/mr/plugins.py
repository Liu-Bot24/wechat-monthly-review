"""Plugins: optional add-ons, installed from a plugin package (.zip) into HOME/插件/<name>/. The program ships none and
knows none by name. A plugin runs inside this program with its rights, so only install code you trust.

A plugin folder holds plugin.json (its description, below), plugin.py and whatever modules it brings. Its settings and
whether it is turned on are kept in config.json in the same folder, so removing the folder removes all of the plugin
except the files it wrote elsewhere; those it names in data_paths(), and uninstall() can move them to the Recycle Bin.

plugin.json:
  name         the folder name, also used on the command line
  title        shown in the window; defaults to name
  version      the plugin's own version
  description  one paragraph for the window
  requires     the oldest version of this program it works with ("1.3.0")
  settings     [{key, label, type: "folder" | "text", help?}]: shown and saved by the window
  actions      [{id, label, help?}]: each one a job of the window, run as `mr plugin <name> <id>`

plugin.py may define, each with settings (this program's) and values (the plugin's own, from config.json):
  defaults(settings) -> dict            values for settings the user has not filled in yet
  prepare(settings, values) -> str      called when the plugin is turned on or its settings are saved; creates what it
                                        needs and returns one line for the window; raises ValueError with the reason to
                                        refuse the values
  status(settings, values) -> dict      {text, todo?: action id}: one line for the window about the plugin's state
  data_paths(settings, values) -> [str] the files and folders it wrote outside its own folder
  after_month(month, settings, say)     called after a month's report has been written, when the plugin is on
  command(args, settings, say) -> int   run by `mr plugin <name> [args…]` (an action runs it with [id])
A plugin that fails to load or raises is reported in one line and skipped: it never fails the month.
"""
import importlib.util, json, re, shutil, sys, tempfile, zipfile
from pathlib import Path

from . import __version__, config

FOLDER = '插件'
MANIFEST = 'plugin.json'
VALUES = 'config.json'
_NAME = re.compile(r'^[^\s/\\:*?"<>|.][^/\\:*?"<>|]{0,59}$')


class PluginError(ValueError):
    """A user-facing reason why a plugin could not be installed, changed or run."""


def folder():
    return config.HOME / FOLDER


def installed():
    """Names of the installed plugins, in order."""
    root = folder()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith('.') and (d / 'plugin.py').is_file())


def _version(v):
    return tuple(int(x) for x in re.findall(r'\d+', str(v or '0'))[:3])


def manifest(name):
    """The plugin's plugin.json; a plugin without one (installed by hand) gets its folder name as title."""
    path = folder() / name / MANIFEST
    m = {}
    if path.is_file():
        try:
            m = json.loads(path.read_text(encoding='utf-8-sig'))
        except ValueError:
            m = {}
    if not isinstance(m, dict):
        m = {}
    m = {'name': name, 'title': m.get('title') or name, 'version': str(m.get('version') or ''),
         'description': m.get('description') or '', 'requires': str(m.get('requires') or ''),
         'settings': [s for s in m.get('settings') or [] if isinstance(s, dict) and s.get('key')],
         'actions': [a for a in m.get('actions') or [] if isinstance(a, dict) and a.get('id')]}
    return m


def values(name):
    """The plugin's own stored values (config.json): its settings and 'enabled'."""
    path = folder() / name / VALUES
    try:
        v = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def _save_values(name, v):
    path = folder() / name / VALUES
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def enabled(name):
    """On unless turned off; a plugin installed before plugins could be turned off counts as on."""
    return values(name).get('enabled', True) is not False


def load(name):
    """The plugin's module. Its own folder is importable while it loads, for the modules it brings."""
    here = folder() / name
    spec = importlib.util.spec_from_file_location(f'mr_plugin_{len(sys.modules)}', here / 'plugin.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(here))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(here))
        except ValueError:
            pass
    return module


def _call(module, hook, *args, default=None):
    f = getattr(module, hook, None)
    return f(*args) if f else default


def effective(name, settings, module=None):
    """The plugin's settings as used: stored values over its defaults."""
    module = module or load(name)
    keys = [s['key'] for s in manifest(name)['settings']]
    out = dict(_call(module, 'defaults', settings, default={}) or {})
    out.update({k: v for k, v in values(name).items() if k in keys and v not in (None, '')})
    return out


def info(name, settings):
    """What the window shows of one plugin."""
    m = manifest(name)
    out = {**m, 'enabled': enabled(name), 'values': {}, 'status': None, 'error': None, 'data_paths': []}
    if m['requires'] and _version(m['requires']) > _version(__version__):
        out['error'] = f'需要消息月报 {m["requires"]} 或更新的版本（现在是 {__version__}）'
        return out
    try:
        module = load(name)
        v = effective(name, settings, module)
        out['values'] = {s['key']: v.get(s['key'], '') for s in m['settings']}
        out['data_paths'] = [str(p) for p in _call(module, 'data_paths', settings, v, default=[]) or []]
        if out['enabled']:
            out['status'] = _call(module, 'status', settings, v)
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {str(e)[:300]}'
    return out


def configure(name, settings, enabled=None, new_values=None):
    """Turn the plugin on or off and/or save its settings. Turning it on or saving while on calls prepare(); a refusal
    there leaves everything as it was. Returns prepare's line, if any."""
    if name not in installed():
        raise PluginError(f'没有安装插件 {name}')
    m = manifest(name)
    stored = values(name)
    keys = {s['key'] for s in m['settings']}
    nxt = dict(stored)
    for k, v in (new_values or {}).items():
        if k not in keys:
            raise PluginError(f'插件 {m["title"]} 没有设置项 {k}')
        nxt[k] = v.strip() if isinstance(v, str) else v
    if enabled is not None:
        nxt['enabled'] = bool(enabled)
    line = None
    if nxt.get('enabled', True) is not False:
        if m['requires'] and _version(m['requires']) > _version(__version__):
            raise PluginError(f'插件 {m["title"]} 需要消息月报 {m["requires"]} 或更新的版本')
        module = load(name)
        v = dict(_call(module, 'defaults', settings, default={}) or {})
        v.update({k: x for k, x in nxt.items() if k in keys and x not in (None, '')})
        try:
            line = _call(module, 'prepare', settings, v)
        except ValueError as e:
            raise PluginError(str(e))
    _save_values(name, nxt)
    return line


def _package_root(z):
    """The folder inside the package that holds plugin.json: the package's top level or its one top folder."""
    names = [n for n in z.namelist() if not n.endswith('/')]
    if MANIFEST in names:
        return ''
    tops = {n.split('/', 1)[0] for n in z.namelist()}
    if len(tops) == 1:
        top = tops.pop() + '/'
        if top + MANIFEST in names:
            return top
    raise PluginError('这不是消息月报的插件包：里面没有 plugin.json')


def install(package):
    """Install or upgrade a plugin from its package (.zip). An upgrade keeps the plugin's settings and on/off state; the
    replaced files go to the Recycle Bin. Returns the plugin's name."""
    package = Path(package)
    try:
        z = zipfile.ZipFile(package)
    except (OSError, zipfile.BadZipFile):
        raise PluginError(f'打不开插件包：{package}')
    with z:
        root = _package_root(z)
        try:
            m = json.loads(z.read(root + MANIFEST).decode('utf-8-sig'))
        except ValueError:
            raise PluginError('插件包里的 plugin.json 不是有效的 JSON')
        name = m.get('name') if isinstance(m, dict) else None
        if not isinstance(name, str) or not _NAME.match(name):
            raise PluginError('插件包里的 plugin.json 没有有效的 name')
        if root + 'plugin.py' not in z.namelist():
            raise PluginError('插件包里没有 plugin.py')
        if m.get('requires') and _version(m['requires']) > _version(__version__):
            raise PluginError(f'这个插件需要消息月报 {m["requires"]} 或更新的版本（现在是 {__version__}）')
        members = []
        for info_ in z.infolist():
            if not info_.filename.startswith(root) or info_.filename.endswith('/'):
                continue
            rel = info_.filename[len(root):]
            parts = Path(rel).parts
            if not rel or Path(rel).is_absolute() or '..' in parts or ':' in rel:
                raise PluginError(f'插件包里有不安全的路径：{info_.filename}')
            members.append((info_, rel))
        folder().mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f'.{name}.', dir=folder()))
        try:
            for info_, rel in members:
                dest = stage / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info_) as src, open(dest, 'wb') as out:
                    shutil.copyfileobj(src, out)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    target = folder() / name
    old = None
    keep = target / VALUES
    if keep.is_file():
        shutil.copyfile(keep, stage / VALUES)
    else:                       # a new plugin starts turned off: the user looks at its settings and turns it on
        (stage / VALUES).write_text(json.dumps({'enabled': False}), encoding='utf-8')
    if target.exists():
        old = folder() / f'.{name}.replaced'
        if old.exists():
            recycle([old])
        target.rename(old)
    stage.rename(target)
    if old is not None:
        recycle([old])
    return name


def uninstall(name, settings, with_data=False):
    """Remove the plugin (to the Recycle Bin), and with_data the files it wrote. Returns the paths moved."""
    if name not in installed():
        raise PluginError(f'没有安装插件 {name}')
    data = []
    if with_data:
        try:
            module = load(name)
            data = [Path(p) for p in _call(module, 'data_paths', settings, effective(name, settings, module), default=[]) or []]
        except Exception as e:
            raise PluginError(f'读不出插件写出的文件（{type(e).__name__}: {str(e)[:200]}），没有卸载')
        data = [p for p in data if p.exists()]
    gone = [folder() / name] + data
    recycle(gone)
    return [str(p) for p in gone]


def after_month(month, settings, say=print):
    for name in installed():
        if not enabled(name):
            continue
        try:
            hook = getattr(load(name), 'after_month', None)
            if hook:
                hook(month, settings, say)
        except Exception as e:          # a plugin never fails the month
            say(f'插件 {name} 出错：{type(e).__name__}: {str(e)[:200]}')


def command(name, args, settings, say=print):
    if name not in installed():
        say(f'没有安装插件 {name}。已安装：' + ('、'.join(installed()) or '无'))
        return 2
    run = getattr(load(name), 'command', None)
    if not run:
        say(f'插件 {name} 没有命令')
        return 2
    return run(list(args), settings, say) or 0


def recycle(paths):
    """Move files and folders to the Windows Recycle Bin (never a permanent delete)."""
    import ctypes
    from ctypes import wintypes
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', ctypes.c_void_p), ('pTo', ctypes.c_void_p),
                    ('fFlags', ctypes.c_ushort), ('fAnyOperationsAborted', wintypes.BOOL), ('hNameMappings', ctypes.c_void_p),
                    ('lpszProgressTitle', wintypes.LPCWSTR)]
    paths = [p for p in paths if Path(p).exists()]
    if not paths:
        return
    names = ctypes.create_unicode_buffer('\0'.join(str(Path(p).resolve()) for p in paths) + '\0\0')
    # FO_DELETE with FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    op = SHFILEOPSTRUCTW(None, 3, ctypes.addressof(names), None, 0x40 | 0x10 | 0x400 | 0x4, False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code or op.fAnyOperationsAborted:
        raise PluginError(f'没能移到回收站（错误 {code}）：' + '、'.join(str(p) for p in paths))
