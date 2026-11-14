"""Checks on an answer that need only the answer and a few facts about what was asked.

make(kind, **facts) gives the callable a run passes to Models.call as validate. It also carries its kind and facts
(.spec), so the very same check can be run again away from the run: `mr check` runs it on the result files of an
external agent, which lets the agent find and fix what the run would refuse before it says it is done.
"""


def ids(value):
    """Message numbers from a model answer, which may give one number where a list is asked for or the reverse,
    or nest lists. Cached answers are read without validation, so every reader goes through this."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in ids(v)]
    return []

def _known(sids, cited):
    """An answer written from this block cites its messages. Mostly unknown numbers mean it was not."""
    shown = set(sids)
    if len(cited) >= 4 and sum(s in shown for s in cited) * 2 < len(cited):
        raise ValueError('引用的消息编号多数不在这一块里')

def cards(answer, sids, allowed):
    """Reading cards: sids are the block's messages, allowed those of them a card may cite (inside the period)."""
    _known(sids, [m for card in answer['cards'] for m in ids(card.get('msg_ids'))])
    keep = set(allowed)
    for card in answer['cards']:
        card['msg_ids'] = [m for m in ids(card.get('msg_ids')) if m in keep]

def scan(answer, sids):
    for field in ('topics', 'links', 'images', 'escalations'):
        rows = answer.get(field)
        answer[field] = [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
    _known(sids, [m for field in ('topics', 'escalations') for x in answer[field] for m in ids(x.get('msg_ids'))])

def at_me(answer, wanted, partial=False):
    """partial: the answer may leave some messages out, as long as it covers at least one; the run asks for the
    ones left out again on their own."""
    got = {s for i in answer['items'] for s in ids(i.get('msg_id'))} & set(wanted)
    missing = set(wanted) - got
    if missing and (not partial or not got):
        raise ValueError(f'漏了 {sorted(missing)}')

def verify(answer, n):
    got = {int(r.get('i', -1)) for r in answer['results']}
    if not set(range(n)) <= got:
        raise ValueError(f'复核漏了卡片 {sorted(set(range(n)) - got)}')

KINDS = {'cards': cards, 'scan': scan, 'at_me': at_me, 'verify': verify}

def make(kind, **facts):
    def check(answer):
        KINDS[kind](answer, **facts)
    check.spec = {'kind': kind, **facts}
    return check

def run(spec, answer):
    facts = dict(spec)
    KINDS[facts.pop('kind')](answer, **facts)
