"""The call records (receipts.jsonl) of a finished result, reduced to what that result cost.

A run that finishes under this version does this itself and exactly (Models.keep_current): it knows which answers
it used. Records left by earlier versions, or by a run of an earlier version, carry no such knowledge; settle()
reduces them by what can still be told from the folder:
  1. failed calls go;
  2. of several calls for the same block, the latest that succeeded stays;
  3. a call whose answer is no longer in the cache goes (the cache was cleared and the month generated again);
  4. where a step still has more blocks than its last run worked on, the most recent ones stay.
What it replaces is kept once in cache/before-usage-cleanup, so a wrong guess can be undone by hand.
"""
import json
from collections import defaultdict
from pathlib import Path

STEP_OF = {'scan_b': 'read', 'read_a': 'read', 'escalate': 'escalate', 'recall_a': 'recall', 'verify': 'verify', 'vision': 'vision',
           'at_me': 'at_me', 'compose_category': 'compose', 'compose_main': 'compose',
           'topic_screen': 'screen', 'topic_read': 'read', 'topic_compose': 'compose'}
ALWAYS = ('topic_plan',)            # asked outside any run; part of what the result cost all the same
FINISHED = ('done', 'done-with-problems')


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None

def current(rows, cache_dir=None, totals=None):
    """Indexes of the rows that stay, in order. rows: the receipts, oldest first. totals: {step: blocks in the last run}."""
    cache_dir, totals = Path(cache_dir) if cache_dir else None, totals or {}
    last = {}
    for i, r in enumerate(rows):
        if r.get('status') == 'ok' and r.get('stage') not in ALWAYS and r.get('key') is not None:
            last[r['stage'], r['key'], r.get('api') == 'external'] = i
    have = {}
    for stage in {k[0] for k in last}:
        d = cache_dir / stage if cache_dir else None
        if d and d.is_dir():
            have[stage] = {f.stem.rsplit('-', 1)[0] for f in d.glob('*.json')}
    kept = {k: i for k, i in last.items() if k[0] not in have or k[1] in have[k[0]]}
    newest = defaultdict(dict)          # step -> {(stage, key): index of its latest call}
    for (stage, key, _), i in kept.items():
        block = newest[STEP_OF.get(stage)]
        block[stage, key] = max(block.get((stage, key), -1), i)
    extra = set()
    for step, blocks in newest.items():
        n = totals.get(step)
        if step and n is not None and len(blocks) > n:
            extra |= set(sorted(blocks, key=blocks.get, reverse=True)[n:])
    return sorted([i for (stage, key, _), i in kept.items() if (stage, key) not in extra]
                  + [i for i, r in enumerate(rows) if r.get('stage') in ALWAYS])

def settle(run_dir, dry=False):
    """Reduce a finished result's records once. Returns (calls before, calls after), or None when there is nothing to do:
    the result is unfinished, was already reduced, or has no records."""
    run_dir = Path(run_dir)
    st, receipts = _load(run_dir / 'status.json'), run_dir / 'receipts.jsonl'
    if not st or st.get('state') not in FINISHED or st.get('usage_current') or not receipts.exists():
        return None
    rows = [json.loads(line) for line in receipts.read_text(encoding='utf-8').splitlines() if line.strip()]
    progress = _load(run_dir / 'progress.json') or {}
    totals = {s['key']: s['total'] for s in progress.get('steps', [])} if progress.get('state') in FINISHED else {}
    keep = current(rows, run_dir / 'cache', totals)
    if dry:
        return len(rows), len(keep)
    if len(keep) != len(rows):
        backup = run_dir / 'cache' / 'before-usage-cleanup' / 'receipts.jsonl'
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(receipts.read_bytes())
        tmp = receipts.with_suffix('.tmp')
        tmp.write_text(''.join(json.dumps(rows[i], ensure_ascii=False) + '\n' for i in keep), encoding='utf-8')
        tmp.replace(receipts)
    st['usage_current'] = True
    if 'tokens_used' in st:             # the overview and the estimates read this; it follows the records
        st['tokens_used'] = sum(rows[i].get('prompt_tokens', 0) + rows[i].get('completion_tokens', 0) for i in keep)
    tmp = run_dir / 'status.tmp'
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(run_dir / 'status.json')
    return len(rows), len(keep)
