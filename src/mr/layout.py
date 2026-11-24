"""Where 消息月报 keeps its files (1.4.0 on), and arranging what an older version left into that place.

  <月报文件夹>\\                       设置里的「月报文件夹」; by default 月报 inside the archive folder
    正式月报\\月报_YYYY-MM.md           the finished report of each month, its links pointing into the archive from here
    原始材料\\                          what a run keeps: YYYY-MM\\ (cards, cache, status, receipts, logs, the working copy of
                                       the report), 语音转写\\, 外部处理\\, 专题\\ … as the output folder held them before 1.4.0
    月度索引\\                          the default place of the 月度索引 plugin, made only when it is turned on

Before 1.4.0 the material lived straight in the output folder of the settings. arrange() moves that folder to
原始材料, points the absolute paths stored in status and job files at the new place, rewrites the relative links of every
Markdown file in it so they still reach the archive, and writes every month's 正式月报.
"""
import json, os, re, shutil
from pathlib import Path
from urllib.parse import quote, unquote

from . import config

MONTH = re.compile(r'^\d{4}-\d{2}$')
# A Markdown link's target: <…> (as this program writes them) or a bare one without spaces.
_LINK = re.compile(r'\]\((<[^>\n]*>|[^)\s<>]+)\)')
_SCHEME = re.compile(r'^(?:[A-Za-z][A-Za-z0-9+.-]+:|[A-Za-z]:[\\/])')     # http:, file:, mailto: …, or a drive


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))

def _inside(path, folder):
    a, b = _norm(path), _norm(folder)
    return a == b or a.startswith(b.rstrip('\\/') + os.sep)

def split_fragment(target):
    """(path, '#', fragment) of a link target, or (target, '', '') when it has none. Folder names in the archive may hold
    a '#' of their own ('… #2', '小组#', a folder named '#'), so only what follows the last '#' is a fragment, and only
    when it is not empty and holds no '/' or '\\'."""
    path, sep, frag = target.rpartition('#')
    if not sep or not frag or '/' in frag or '\\' in frag:
        return target, '', ''
    return path, sep, frag

def rebase_links(text, old_dir, new_dir, moved=None):
    """text was written in old_dir; it is now read from new_dir. Every relative link keeps reaching what it reached:
    a target inside moved[0] has gone along to the same place under moved[1]. Links with a scheme, absolute paths and
    links to an anchor on the same page are left as they are."""
    old_dir, new_dir = Path(old_dir), Path(new_dir)

    def one(m):
        raw = m.group(1)
        if raw.startswith('<'):
            path, sep, frag = split_fragment(raw[1:-1])
        else:                                    # split first: a %23 is part of the path, not a fragment
            path, sep, frag = split_fragment(raw)
            path = unquote(path)
        if not path or path == '#' or _SCHEME.match(path) or path.startswith(('/', '\\')):
            return m.group(0)
        where = os.path.normpath(os.path.join(old_dir, path))
        if moved and _inside(where, moved[0]):
            where = os.path.join(moved[1], os.path.relpath(where, moved[0]))
        try:
            rel = Path(os.path.relpath(where, new_dir)).as_posix()
        except ValueError:                       # another drive: only an absolute link reaches it
            return '](' + 'file:///' + quote(Path(where).as_posix(), safe='/:') + sep + frag + ')'
        return '](<' + rel + sep + frag + '>)'

    return _LINK.sub(one, text)


def _write(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


# ---------- 正式月报 ----------
def report_name(month):
    return f'月报_{month}.md'

def published(settings, month):
    d = config.final_dir(settings)
    return d / report_name(month) if d else None

def publish(settings, month):
    """Copy the month's finished report from 原始材料 to 正式月报, its links rewritten to start from there; replaces an
    earlier copy. Returns the copy's path, or None when the month has no report."""
    out = str(settings.get('output_root') or '').strip()
    dest = published(settings, month)
    if not out or dest is None:
        return None
    run_dir = Path(out) / month
    src = run_dir / report_name(month)
    if not src.exists():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    _write(dest, rebase_links(src.read_text(encoding='utf-8'), run_dir, dest.parent))
    return dest

def publish_all(settings, say=print):
    out = str(settings.get('output_root') or '').strip()
    done = []
    if out and Path(out).is_dir():
        for d in sorted(Path(out).iterdir()):
            if d.is_dir() and MONTH.match(d.name) and publish(settings, d.name):
                done.append(d.name)
    say(f'正式月报：写出 {len(done)} 个月')
    return done


# ---------- an older version's material ----------
def _has_material(folder):
    try:
        for d in Path(folder).iterdir():
            if d.is_dir() and (MONTH.match(d.name) and any((d / n).exists() for n in ('status.json', 'plan.json', report_name(d.name)))
                               or d.name in ('专题', '语音转写', '外部处理') and any(d.iterdir())):
                return True
    except OSError:
        return False
    return False

def legacy_root(settings):
    """The folder an older version kept its material in, when it still has some and is not this version's 原始材料."""
    v = str(settings.get('legacy_output_root') or '').strip()
    if v == '-':
        return None
    cand = Path(v) if v else config.OLD_DEFAULT_OUTPUT
    out = str(settings.get('output_root') or '').strip()
    if not cand.is_dir() or (out and _norm(cand) == _norm(out)) or not _has_material(cand):
        return None
    return cand

def state(settings):
    """What the window shows about the layout: an older version's folder still to arrange, and why it cannot be yet."""
    old = legacy_root(settings)
    if old is None:
        return {'legacy': None}
    months = sorted(d.name for d in old.iterdir() if d.is_dir() and MONTH.match(d.name))
    return {'legacy': str(old), 'months': len(months), 'target': settings.get('output_root') or '',
            'final': str(config.final_dir(settings) or ''), 'problem': problem(settings, old)}

def problem(settings, old):
    target = str(settings.get('output_root') or '').strip()
    if not target:
        return '还没有设置归档目录，月报文件夹无从确定：请先在 设置 › 常规 中选择归档目录。'
    if _inside(target, old) or _inside(old, target):
        return f'旧的月报文件夹和新的原始材料文件夹互相包含，不能自动整理：{old} → {target}'
    t = Path(target)
    if t.exists() and (not t.is_dir() or any(t.iterdir())):
        return f'原始材料文件夹里已经有内容，不能把旧的材料并进去：{target}'
    return None

def _held_locks(old):
    from .jobs import lock_holder
    names = [d.name for d in Path(old).iterdir() if d.is_dir() and MONTH.match(d.name)] + ['asr']
    return [n for n in names if lock_holder(old, n)]


def _fix_paths(value, old, new):
    """value with every string that is old, or a path inside it, pointing at the same place under new."""
    if isinstance(value, str):
        if value and (os.path.isabs(value) or value.startswith('\\\\')) and _inside(value, old):
            return os.path.join(new, os.path.relpath(os.path.normpath(value), old)) if _norm(value) != _norm(old) else str(new)
        return value
    if isinstance(value, list):
        return [_fix_paths(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _fix_paths(v, old, new) for k, v in value.items()}
    return value

def _rewrite_json(path, old, new):
    try:
        raw = path.read_text(encoding='utf-8')
        data = json.loads(raw)
    except (OSError, ValueError):
        return False
    fixed = _fix_paths(data, old, new)
    if fixed == data:
        return False
    _write(path, json.dumps(fixed, ensure_ascii=False, indent=2 if '\n ' in raw else None))
    return True

def swap_root(text, old, new):
    """text with the absolute path old, and every path inside it, written from new instead (a task's result file, the
    folders an agent is pointed at)."""
    parts = [re.escape(x) for x in re.split(r'[\\/]', os.path.normpath(str(old)).rstrip('\\/'))]
    pat = re.compile(r'(?<![\w\\/])' + r'([\\/])'.join(parts) + r'(?!\w)', re.I)

    def one(m):
        sep = next((g for g in m.groups() if g), '\\')
        return os.path.normpath(str(new)).replace('\\', sep)
    return pat.sub(one, text)

# Files that keep absolute paths (status: the report and text written; progress: the report; jobs: logs and reports).
STATE_FILES = ('status.json', 'progress.json', 'plan-progress.json', 'plan.json')

def fix_moved(new, old, home=None):
    """After the material folder moved from old to new: stored absolute paths and Markdown links follow it.
    Returns (json files changed, Markdown files changed)."""
    new, old = Path(new), Path(old)
    js = md = 0
    for path in new.rglob('*'):
        if not path.is_file():
            continue
        if path.name in STATE_FILES:
            js += _rewrite_json(path, old, new)
        elif path.suffix.lower() == '.md':
            text = path.read_text(encoding='utf-8', errors='surrogateescape')
            rel = path.parent.relative_to(new)
            fixed = swap_root(rebase_links(text, old / rel, path.parent, moved=(old, new)), old, new)
            if fixed != text:
                path.write_text(fixed, encoding='utf-8', errors='surrogateescape')
                md += 1
    jobs_file = Path(home or config.HOME) / 'jobs.json'
    if jobs_file.exists():
        js += _rewrite_json(jobs_file, old, new)
    return js, md


def arrange(settings=None, say=print):
    """Move an older version's material folder to 原始材料 of the 月报 folder and finish the new layout (see the module
    docstring). Refuses, changing nothing, while a run holds one of its months or when the place is taken.
    Returns the exit code: 0 done (or nothing to do), 2 refused."""
    settings = settings or config.load_settings()
    old = legacy_root(settings)
    if old is None:
        say('没有需要整理的旧版月报材料。')
        return 0
    why = problem(settings, old)
    if why:
        say(why)
        return 2
    held = _held_locks(old)
    if held:
        say('这些月份正在被处理，先等它们结束：' + '、'.join(held))
        return 2
    target = Path(settings['output_root'])
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rmdir()                             # empty, checked by problem()
    say(f'移动 {old} → {target}')
    try:
        os.rename(old, target)
    except OSError as e:
        if getattr(e, 'winerror', None) != 17 and e.errno != 18:     # not "another drive": a file is open, or no right
            say(f'移动失败，没有改动任何文件：{e}')
            return 2
        say('不在同一个磁盘上：先复制，复制完再把旧文件夹移到回收站')
        from .plugins import recycle
        try:
            shutil.copytree(old, target, copy_function=shutil.copy2)
        except (OSError, shutil.Error) as e:
            if target.exists():
                recycle([target])                       # only the partial copy just made; the old folder is untouched
            say(f'复制失败，旧文件夹没有改动：{e}')
            return 2
        recycle([old])
    js, md = fix_moved(target, old)
    say(f'记录里的路径改了 {js} 个文件，Markdown 里的链接改了 {md} 个文件')
    patch = {'legacy_output_root': '-'}
    ext = str(settings.get('external_root') or '').strip()
    if ext and _inside(ext, old):
        patch['external_root'] = str(target / os.path.relpath(os.path.normpath(ext), old))
    config.save_settings(patch)
    publish_all(config.load_settings(), say)
    say('整理完成。')
    return 0


def dismiss():
    """Stop asking about an older version's folder: it stays where it is, unused."""
    config.save_settings({'legacy_output_root': '-'})
