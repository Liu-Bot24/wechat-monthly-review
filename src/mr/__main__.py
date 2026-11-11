"""mr: monthly review of the WeChat archive. Reads and writes the same user configuration as the window (see config.py).

  python -m mr plan 2026-10         extract and chunk without calling any model; print the plan
  python -m mr run 2026-10          run every stage; resumes from cache; stops at the budget
  python -m mr run 2026-10 --budget-tokens 3000000   this run's cap instead of the monthly budget in the settings
  python -m mr run 2026-10 --api-only                 stages handed to an external agent are asked of their models this time
  python -m mr status 2026-10       progress, tokens used and what is left

  python -m mr asr --from 2026-01 --to 2026-08 [--chat 微信号…]
                                    transcribe voice clips of these months ahead of any report

  python -m mr topic plan|screen|run <专题编号> [--budget-tokens N] [--api-only]
                                    a 专题 made in the window: preview (no model calls), model screening only, or the whole run

  python -m mr check <环节文件夹…> [--wait [分钟]]
                                    for an external agent: are all its result files there, and would the run accept them
                                    (changes no result). Exit code 0: every task passes; 1: tasks to do or redo.
                                    --wait: when every task passes, hold on (9 minutes unless given) for the next batch;
                                    0: nothing more will come; 1: tasks to do; 3: none came in that time.

  python -m mr chats [2026-10] [--find 关键词] [--category <分类>] [--state read|skip] [--all]
                                    chats active in the month (--all: every chat) with counts and whether they are read
  python -m mr select show          the current choice
  python -m mr select exclude <会话…>        skip these chats (WeChat ID, exact name, or a unique part of a name)
  python -m mr select include <会话…>        read these chats even though their category is skipped
  python -m mr select clear <会话…>          drop the hand-made choice for these chats
  python -m mr select skip-category <分类…>  skip whole categories
  python -m mr select read-category <分类…>  read them again
"""
import argparse, json, sys, time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from . import ROOT, config
from .archive import Archive, month_bounds, stamp, within
from .pipeline import Pipeline
from .selection import Selection, overview, resolve

def _latest_month(archive):
    """The last month that has ended, or the month of the newest message if that is earlier."""
    this_month = stamp(time.time()).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return min((this_month - timedelta(days=1)).strftime('%Y-%m'), stamp(archive.latest() - 1).strftime('%Y-%m'))

def chats(settings, month, find, category, state, show_all):
    archive = Archive(settings['archive_root'], settings['output_root'])
    selection = Selection(config.selection_path(), settings['default_skip_categories'])
    month = month or _latest_month(archive)
    rows = overview(archive, selection, *month_bounds(month))
    if not show_all:
        rows = [r for r in rows if r['messages']]
    if find:
        rows = [r for r in rows if find.lower() in r['name'].lower() or find in r['chat']]
    if category:
        rows = [r for r in rows if within(r['category'], category)]
    if state:
        rows = [r for r in rows if (r['skip'] is None) == (state == 'read')]
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r['category']].append(r)
    print(f'# {month} 会话一览（{len(rows)} 个；选择文件：{selection.path}）')
    for cat in sorted(by_cat):
        rs = sorted(by_cat[cat], key=lambda r: -r['messages'])
        print(f'\n## {cat}（{len(rs)} 个，本月 {sum(r["messages"] for r in rs):,} 条）')
        for r in rs:
            last = stamp(r['last']).strftime('%m-%d') if r['last'] else '  -  '
            print(f'{"跳过" if r["skip"] else "读取"}  {r["messages"]:>6,} 条  我 {r["mine"]:>4}  最后 {last}  {r["name"]}  ({r["chat"]})'
                  + (f'  ← {r["skip"]}' if r['skip'] else ''))

def select(settings, action, targets):
    archive = Archive(settings['archive_root'], settings['output_root'])
    selection = Selection(config.selection_path(), settings['default_skip_categories'])
    if action == 'show':
        print(f'选择文件：{selection.path}{"" if selection.path.exists() else "（还没有保存过，以下是默认值）"}')
        print('整类跳过：' + ('、'.join(selection.skip_categories) or '无'))
        print(f'手动排除 {len(selection.exclude)} 个：' + ('、'.join(f'{n}（{c}）' for c, n in selection.exclude.items()) or '无'))
        print(f'手动保留 {len(selection.include)} 个：' + ('、'.join(f'{n}（{c}）' for c, n in selection.include.items()) or '无'))
        return 0
    if not targets:
        print('请给出会话或分类。')
        return 2
    if action in ('skip-category', 'read-category'):
        known = set(archive.categories())
        unknown = [t for t in targets if t not in known]
        if unknown:
            print('没有这些分类：' + '、'.join(unknown) + '\n现有分类：' + '、'.join(sorted(known)))
            return 2
        for t in targets:
            selection.set_category(t, action == 'skip-category')
        selection.save()
        print(f'已{"整类跳过" if action == "skip-category" else "恢复读取"}：' + '、'.join(targets))
        return 0
    found, problems = resolve(archive, targets)
    if problems:
        # All or nothing: a half-applied list is harder to check than a refused one.
        print('没有做任何修改：\n' + '\n'.join(f'- {p}' for p in problems))
        return 2
    for chat, name in found:
        selection.set_chat(chat, name, action)
    selection.save()
    for chat, name in found:
        reason = selection.reason(chat, archive.category(chat), chat in archive.excluded)
        print(f'- {name}（{chat}）：{"跳过，" + reason if reason else "读取"}')
    return 0

def _more_coming(folder):
    """Whether 消息月报 may still write tasks into this folder (a stage's, or the handoff root): a month or 专题 is
    running, or is waiting for agents with every result in (it then goes on by itself), and that run has not yet
    passed the point where this stage's tasks are written."""
    from . import external
    settings = config.load_settings()
    root, out = external.root(settings).resolve(), Path(settings['output_root'])
    try:
        rel = Path(folder).resolve().relative_to(root)
    except ValueError:
        return False
    stage = rel.parts[0] if rel.parts else None             # None: the root, every stage
    return _going(settings, out, external, lambda still: still is None or (stage in still if stage else bool(still)))

def _going(settings, out, external, wanted):
    from . import topic
    from .jobs import alive
    from .progress import read as read_progress
    runs = list(out.glob('[0-9][0-9][0-9][0-9]-[0-9][0-9]/progress.json')) + list(topic.root_dir(settings).glob('*/progress.json'))
    for path in runs:
        progress = read_progress(path) or {}
        going = progress.get('state') == 'running' and alive(progress.get('pid'))
        if not going and progress.get('state') == 'external-wait':
            found = external.owner_tally(settings, path.parent.relative_to(out))
            going = found['tasks'] and found['results'] >= found['tasks']
        if going and wanted(progress.get('handoff_open')):          # the stage folders that run may yet write into
            return True
    return False

def main(argv=None):
    p = argparse.ArgumentParser(prog='mr')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('plan', 'run', 'status'):
        s = sub.add_parser(name)
        s.add_argument('month')
        if name == 'run':
            s.add_argument('--budget-tokens', type=int)
            s.add_argument('--until', choices=['read', 'compose'], help='stop after this stage')
            s.add_argument('--api-only', action='store_true', help='ask the models, not the external agent')
    s = sub.add_parser('asr')
    s.add_argument('--from', dest='first', required=True)
    s.add_argument('--to', dest='last', required=True)
    s.add_argument('--chat', action='append', help='only these chats (WeChat IDs), whatever the chat selection says')
    s = sub.add_parser('topic')
    s.add_argument('action', choices=['plan', 'screen', 'run'])
    s.add_argument('id')
    s.add_argument('--budget-tokens', type=int)
    s.add_argument('--api-only', action='store_true')
    s = sub.add_parser('check')
    s.add_argument('folder', nargs='+')
    s.add_argument('--wait', nargs='?', type=float, const=9.0, default=None, metavar='分钟')
    s = sub.add_parser('chats')
    s.add_argument('month', nargs='?')
    s.add_argument('--find')
    s.add_argument('--category')
    s.add_argument('--state', choices=['read', 'skip'])
    s.add_argument('--all', action='store_true', help='also list chats with no messages in the month')
    s = sub.add_parser('select')
    s.add_argument('action', choices=['show', 'exclude', 'include', 'clear', 'skip-category', 'read-category'])
    s.add_argument('targets', nargs='*')
    args = p.parse_args(argv)
    if args.command == 'check':
        # Run by an agent in whatever terminal it has: needs no settings and no archive, and must not die on a character
        # the terminal cannot show.
        from . import external
        sys.stdout.reconfigure(errors='replace')
        if args.wait is None and not any(Path(f).is_dir() for f in args.folder):
            print('找不到这些文件夹：' + '、'.join(args.folder))
            return 2
        if args.wait is None:
            text, done = external.check_report(args.folder)
            print(text)
            return 0 if done else 1
        text, code = external.check_wait(args.folder, args.wait * 60, lambda: any(_more_coming(f) for f in args.folder))
        print(text)
        return code
    settings = config.load_settings()
    if args.command == 'chats':
        return chats(settings, args.month, args.find, args.category, args.state, args.all)
    if args.command == 'select':
        return select(settings, args.action, args.targets)
    if args.command == 'topic':
        from .jobs import month_lock
        from .topic import TopicRun
        run = TopicRun(ROOT, args.id, settings)
        with month_lock(settings['output_root'], run.month):
            return run.plan() if args.action == 'plan' else run.run(budget_tokens=args.budget_tokens, until='screen' if args.action == 'screen' else None,
                                                                    api_only=args.api_only)
    if args.command == 'asr':
        from . import voice
        from .jobs import month_lock
        with month_lock(settings['output_root'], 'asr'):
            return voice.run(settings, config.load_models(), args.first, args.last, args.chat)
    pipe = Pipeline(ROOT, args.month, settings)
    if args.command == 'plan':
        pipe.plan()
    elif args.command == 'status':
        pipe.status()
    else:
        from .jobs import month_lock
        with month_lock(settings['output_root'], args.month):
            return pipe.run(budget_tokens=args.budget_tokens, until=args.until, api_only=args.api_only)

if __name__ == '__main__':
    sys.exit(main())
