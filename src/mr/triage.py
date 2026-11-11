"""S2: decide which lines get careful reading (A) and which get scanned (B), then cut chunks."""
import hashlib
from .archive import ancestors
from .extract import render, tokens

GAP = 2 * 3600

def _segments(lines):
    """Index ranges of conversations: a new one starts after a quiet gap of two hours."""
    start = 0
    for i in range(1, len(lines) + 1):
        if i == len(lines) or lines[i]['ts'] - lines[i - 1]['ts'] > GAP:
            yield start, i
            start = i

def tier_lines(packet, settings):
    """Return (a_lines, b_lines).

    Whole chats go to A when they are private, work or business chats, small groups, or groups where I spoke a lot.
    In other groups A gets each conversation I took part in: the whole of it when it is short, a wide window otherwise.
    """
    small = packet['group'] and packet['members'] and packet['members'] <= settings['small_group_members']
    active = settings['active_group']
    busy_with_me = packet['self_messages'] >= active['self_messages'] and packet['messages'] <= active['max_messages']
    if not packet['group'] or any(a in settings['whole_chat_a_categories'] for a in ancestors(packet['category'])) or small or busy_with_me:
        return packet['lines'], []
    lines = packet['lines']
    hot = set(packet['at_me']) | set(packet['quote_me'])
    w = settings['window_lines']
    keep = set()
    for s, e in _segments(lines):
        marks = [i for i in range(s, e) if lines[i]['in_month'] and (lines[i]['self'] or lines[i]['sid'] in hot)]
        if not marks:
            continue
        if e - s <= settings['segment_lines']:
            keep.update(range(s, e))
        else:
            for i in marks:
                keep.update(range(max(s, i - w), min(e, i + w + 1)))
    a = [l for i, l in enumerate(lines) if i in keep]
    b = [l for i, l in enumerate(lines) if i not in keep and l['in_month']]
    return a, b

def _split(lines, limit):
    """Cut at a day change or a quiet gap when the chunk is getting full."""
    chunks, current, size = [], [], 0
    for l in lines:
        t = tokens(l['text']) + 6
        boundary = current and (l['ts'] - current[-1]['ts'] > GAP or l['ts'] // 86400 != current[-1]['ts'] // 86400)
        if current and (size + t > limit or (boundary and size > limit * 0.6)):
            chunks.append(current)
            current, size = [], 0
        current.append(l)
        size += t
    if current:
        chunks.append(current)
    return chunks

def me_line(packet, lines, span='本月'):
    """How much of this text is mine, counted by code, so a model never has to guess who I am."""
    n = sum(1 for l in lines if l['in_month'] and l['self'])
    text = f'我的发言：这段 {n} 条，{span}在这个会话共 {packet["self_messages"]} 条。说话人写作"我"的才是我发的。'
    if not packet['self_messages']:
        text += f'{span}我在这个会话里没有发言，下面每一句都是别人说的。'
    return text + '\n'

def _header(packet, part, total, lines):
    kind = '私聊' if not packet['group'] else f'群聊（约 {packet["members"]} 人）' if packet['members'] else '群聊'
    return f'会话：{packet["name"]}\n分类：{packet["category"]}\n类型：{kind}\n本块：第 {part}/{total} 块\n{me_line(packet, lines)}\n'

def build_chunks(packets, settings):
    chunks = []
    overlap = settings['overlap_lines']
    for p in packets:
        a, b = tier_lines(p, settings)
        p['tier'] = 'A' if not b else 'A+B' if a else 'B'
        for stage, lines in (('read_a', a), ('scan_b', b)):
            if not any(l['in_month'] for l in lines):
                continue
            parts = _split(lines, settings['chunk_tokens'][stage])
            for i, part in enumerate(parts, 1):
                if not any(l['in_month'] for l in part):
                    continue
                # The end of the previous chunk comes first, so a conversation cut at the boundary still reads whole.
                # Its ids are not in this chunk's sids, so no card can be written for it twice.
                tail = parts[i - 2][-overlap:] if i > 1 and overlap else []
                lead = (f'（下面先附上一块的最后 {len(tail)} 条，只用于理解，不要为它写内容）\n' + render(tail)
                        + '\n\n（本块从这里开始）\n') if tail else ''
                text = _header(p, i, len(parts), part) + lead + render(part)
                key = hashlib.sha256(f'{stage}\n{p["chat"]}\n{text}'.encode('utf-8')).hexdigest()[:20]
                chunks.append({'key': key, 'stage': stage, 'chat': p['chat'], 'text': text,
                               'sids': [l['sid'] for l in part], 'est_tokens': tokens(text)})
    return chunks
