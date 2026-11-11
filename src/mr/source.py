"""What changed in the archive for a month since its report was written, so I can judge whether to write it again.

A run records how many messages each chat had (status.json: source_chats). With that, the change is exact per chat.
Reports from before it was recorded only have the month's total. For those, the report's own appendix says how many
chats and messages it read in each category, which is compared with what a run would read now; the chats whose
documents were written again after the run read them are named as well.
"""
import re
from collections import Counter
from datetime import datetime
from .archive import TZ, month_bounds
from .selection import overview


def counts(archive, month):
    """{chat: messages in the month} as the archive stands now."""
    return dict(archive.active_chats(*month_bounds(month)))

def big_enough(then, now):
    """Small differences are not worth a notice: under 50 messages, or under half a percent."""
    return abs(now - then) >= max(50, then * 0.005)

def read_at(st, progress=None):
    """When the run behind the report read the archive (epoch seconds), as closely as it is known."""
    if st.get('source_read_at'):
        return st['source_read_at']
    if progress and progress.get('started') and progress.get('state') in ('done', 'done-with-problems'):
        return progress['started']
    try:
        return datetime.fromisoformat(st['updated']).replace(tzinfo=TZ).timestamp()
    except (KeyError, TypeError, ValueError):
        return None

_ROW = re.compile(r'^\| (.+?) \| ([\d,]+) \| ([\d,]+) \|')

def covered(report):
    """What a report says it read, from its appendix: {'categories': {category: (chats, messages)}, 'skipped': messages}."""
    part = re.search(r'\n### 覆盖\n(.*?)(?=\n### |\Z)', report or '', re.S)
    if not part:
        return None
    number = lambda s: int(s.replace(',', ''))
    cats = {r.group(1): (number(r.group(2)), number(r.group(3))) for r in map(_ROW.match, part.group(1).splitlines()) if r}
    skipped = sum(number(n) for n in re.findall(r'^跳过：.* 个会话 / ([\d,]+) 条', part.group(1), re.M))
    return {'categories': cats, 'skipped': skipped} if cats else None

def detail(archive, selection, month, st, progress=None, report=None):
    """{'then', 'now', 'chats', 'categories', 'reading', 'skipped', 'rewritten'}: the totals, and what lies behind them.
    chats: the exact change per chat, when the run recorded its counts. Otherwise, for an older report (its text in
    `report`): categories, what it read per category against what a run would read now, with the totals inside and
    outside the reading range (reading, skipped); and rewritten, the chats written again since the run read them.
    Rows about chats say whether a run reads that chat."""
    start, end = month_bounds(month)
    rows = {r['chat']: r for r in overview(archive, selection, start, end) if r['messages']}
    def row(chat, **more):
        known = chat in archive.folders
        category = archive.category(chat) if known else ''
        return {'name': archive.names.get(chat, chat), 'category': category,
                'read': known and not selection.reason(chat, category, chat in archive.excluded), **more}
    out = {'then': st.get('source_messages'), 'now': sum(r['messages'] for r in rows.values()),
           'chats': None, 'categories': None, 'reading': None, 'skipped': None, 'rewritten': None}
    then = st.get('source_chats')
    if isinstance(then, dict):
        changed = [row(c, then=then.get(c, 0), now=rows[c]['messages'] if c in rows else 0) for c in set(then) | set(rows)]
        out['chats'] = sorted((c for c in changed if c['then'] != c['now']), key=lambda c: (-abs(c['now'] - c['then']), c['name']))
    else:
        was = covered(report)
        if was:
            chats, messages = Counter(), Counter()
            for r in rows.values():
                if not r['skip']:
                    chats[r['category']] += 1
                    messages[r['category']] += r['messages']
            cats = [{'category': c, 'then_chats': was['categories'].get(c, (0, 0))[0], 'then': was['categories'].get(c, (0, 0))[1],
                     'now_chats': chats[c], 'now': messages[c]} for c in set(was['categories']) | set(messages)]
            out['categories'] = sorted((c for c in cats if (c['then_chats'], c['then']) != (c['now_chats'], c['now'])),
                                       key=lambda c: (-abs(c['now'] - c['then']), c['category']))
            out['reading'] = {'then': sum(m for _, m in was['categories'].values()), 'now': sum(messages.values())}
            out['skipped'] = {'then': was['skipped'], 'now': out['now'] - sum(messages.values())}
        since = read_at(st, progress)
        if since is not None:
            again = archive.rewritten_since(start, end, since)
            out['rewritten'] = sorted((row(c, now=rows[c]['messages']) for c in again if c in rows), key=lambda c: (-c['now'], c['name']))
    return out

def accept(archive, month, st):
    """Take the archive as it is now as what the report stands on; the notice goes until it changes again."""
    now = counts(archive, month)
    st['source_accepted'] = {'at': datetime.now().isoformat(timespec='seconds'), 'then': st.get('source_messages'), 'now': sum(now.values())}
    st['source_messages'], st['source_chats'] = sum(now.values()), now
    return st
