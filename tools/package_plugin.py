"""Make a plugin package (.zip) from a plugin folder under plugins/: what 设置 › 插件 installs.

  python tools/package_plugin.py plugins/月度索引 [dest folder]   ->  <dest>/<name>-<version>.zip (default dest: dist/)

The package holds the folder under the plugin's name, without its tests, caches or stored settings.
"""
import json, sys, zipfile
from pathlib import Path

LEAVE_OUT = {'tests', '__pycache__', 'config.json', 'progress.json', 'run.log'}


def package(src, dest):
    src, dest = Path(src), Path(dest)
    m = json.loads((src / 'plugin.json').read_text(encoding='utf-8-sig'))
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f'{m["name"]}-{m["version"]}.zip'
    tmp = out.with_suffix('.tmp')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob('*')):
            rel = p.relative_to(src)
            if p.is_dir() or LEAVE_OUT & set(rel.parts) or p.suffix in ('.pyc', '.tmp'):
                continue
            z.write(p, f'{m["name"]}/{rel.as_posix()}')
    tmp.replace(out)
    return out


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = Path(__file__).resolve().parents[1]
    print(package(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else root / 'dist'))
