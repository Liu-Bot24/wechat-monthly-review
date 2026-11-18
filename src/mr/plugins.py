"""Plugins: optional code installed beside the configuration, in HOME/插件/<name>/plugin.py. The program ships none and
knows none by name; removing a plugin's folder uninstalls it. A plugin runs inside this program with its rights, so
only install code you trust.

A plugin's plugin.py may define:
  after_month(month, settings, say)   called after a month's report has been written
  command(args, settings, say) -> int run by `mr plugin <name> [args…]`
A plugin that fails to load or raises is reported in one line and skipped: it never fails the month.
"""
import importlib.util, sys

from . import config

FOLDER = '插件'


def folder():
    return config.HOME / FOLDER


def installed():
    """Names of the installed plugins, in order."""
    root = folder()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if (d / 'plugin.py').is_file())


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


def after_month(month, settings, say=print):
    for name in installed():
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
