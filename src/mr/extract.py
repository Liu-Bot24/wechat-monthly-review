"""S1: turn one month of the archive into compact, masked packets plus exact signals."""
import re
from .archive import Archive, stamp
from .mask import Masker

_URL = re.compile(r'https?://([^/\s]+)\S*')
_KEEP_SYSTEM = re.compile(r'加入|邀请|移出|退出|群名|撤回')
_QUOTE_START = re.compile(r'^引用 [^：\n]*：', re.M)
BODY_LIMIT = 8000          # one message; forwarded chat bundles get FORWARD_LIMIT
FORWARD_LIMIT = 20000
ASR_MARK = '〔语音转写，可能有识别错误〕'
MEDIA_KINDS = {'图片', '视频', '语音'}

def tokens(text):
    """Rough count for chunking; calibrated later against real usage (about 0.7 per Chinese character)."""
    return int(len(text) * 0.75) + 1

def strip_quote(body):
    """The reply without the quoted text the archive appends after it ("引用 名称：…", possibly over several lines);
    used when the quoted message itself is linked."""
    m = _QUOTE_START.search(body)
    return body[:m.start()].strip() if m else body

def _body(archive, row, masker, previous, where, transcript=None):
    body = archive.readable_body(row['content'] or '', row['type'], row['sender'])
    if transcript:
        body = f'{body}{ASR_MARK}{transcript}'
    # Links are cut to their site first, so tokens in query strings never reach a model or the mask list.
    body = _URL.sub(lambda m: f'‹{m.group(1)}›', body)
    body = masker.mask(body, context=previous, where=where)
    limit = FORWARD_LIMIT if body.startswith('转发 ') or '\n转发 ' in body else BODY_LIMIT
    if len(body) > limit:
        body = body[:limit] + '……（过长截断，原文见归档）'
    return body.strip()

def masked_values(archive, row):
    """The original values masking replaced in one message, in order, for showing to me on this computer only."""
    masker = Masker(keep_values=True)
    _body(archive, row, masker, '', None)
    return [f['value'] for f in masker.found]

def extract_month(archive: Archive, month_start, month_end, context_hours, selection, transcripts=None, active=None):
    """transcripts: {anchor: text} for voice messages, made before extraction.
    active: archive.active_chats(month_start, month_end) when the caller already has it.
    The span need not be a month; a 专题 passes its own dates."""
    transcripts = transcripts or {}
    masker = Masker()
    margin = context_hours * 3600
    packets, index, skipped = [], {}, []
    counter = 0
    for chat, n_month in (active if active is not None else archive.active_chats(month_start, month_end)):
        category = archive.category(chat)
        name = archive.names.get(chat, chat)
        reason = selection.reason(chat, category, chat in archive.excluded)
        if reason:
            # Dropped before any message is read, so nothing from this chat reaches a model.
            skipped.append({'chat': chat, 'name': name, 'category': category, 'messages': n_month, 'reason': reason})
            continue
        is_group = archive.is_group(chat)
        rows = archive.messages(chat, month_start - margin, month_end + margin)
        by_anchor, sender_of = {}, {r['anchor']: r['sender'] for r in rows}
        lines, members, at_me, quote_me, self_n, at_all = [], None, [], [], 0, 0
        previous = ''
        for row in rows:
            in_month = month_start <= row['timestamp'] < month_end
            is_self = row['sender'] == archive.self_id or chat == 'filehelper'
            kind = row['type']
            if kind == '系统消息' and not _KEEP_SYSTEM.search(row['content'] or ''):
                continue
            if kind == '动画表情' and lines and lines[-1]['kind'] == '动画表情':
                lines[-1]['count'] += 1
                continue
            counter += 1
            sid = f'm{counter}'
            by_anchor[row['anchor']] = sid
            if is_group:
                members = row['members'] or members
                if in_month and archive.self_id in row['mentions']:
                    at_me.append(sid)
                if in_month and 'notify@all' in row['mentions']:
                    at_all += 1
            body = _body(archive, row, masker, previous, {'chat': chat, 'sid': sid},
                         transcripts.get(row['anchor']) if kind == '语音' else None)
            quoted = row['quote']
            reply_to = None
            if quoted:
                reply_to = by_anchor.get(quoted)
                target = sender_of[quoted] if quoted in sender_of else archive.sender_of(chat, quoted, row['timestamp'])
                if in_month and target == archive.self_id and not is_self:
                    quote_me.append(sid)
                if reply_to:
                    body = strip_quote(body)
            if in_month and is_self:
                self_n += 1
            sender = '我' if is_self else (name if not is_group else archive.person(row['sender']))
            lines.append({'sid': sid, 'ts': row['timestamp'], 'sender': sender, 'from': row['sender'], 'self': is_self, 'kind': kind,
                          'text': body, 'reply_to': reply_to, 'in_month': in_month, 'count': 1})
            index[sid] = {'chat': chat, 'ts': row['timestamp'], 'in_month': in_month, 'lookup_id': None,
                          'doc': row['doc'], 'anchor': row['anchor'], 'kind': kind, 'self': is_self, 'reply_to': reply_to}
            previous = body
        unanswered = None
        if not is_group and chat != 'filehelper':
            month_lines = [l for l in lines if l['in_month'] or l['ts'] >= month_end]
            if month_lines and not month_lines[-1]['self'] and month_lines[-1]['in_month'] and any(l['self'] for l in lines):
                unanswered = month_lines[-1]['sid']
        packets.append({'chat': chat, 'name': name, 'category': category, 'group': is_group, 'members': members,
                        'messages': n_month, 'self_messages': self_n, 'at_me': at_me, 'quote_me': quote_me,
                        'at_all': at_all, 'unanswered': unanswered, 'lines': lines})
    numbers = archive.ids([(i['doc'], i['anchor']) for i in index.values()])
    for i in index.values():
        i['lookup_id'] = numbers.get(i['anchor'])
    return packets, index, skipped, masker.found

def render(lines, start_month=None, end_month=None):
    """Packet text: one header per day, `m17 14:22 张三：内容`."""
    out, day = [], None
    for l in lines:
        d = stamp(l['ts'])
        label = d.strftime('%Y-%m-%d') + ('' if l['in_month'] else '（上下文，只用于理解）')
        if label != day:
            out.append(f'## {label}')
            day = label
        text = l['text']
        if l['kind'] == '动画表情':
            text = f'[表情×{l["count"]}]' if l['count'] > 1 else '[表情]'
        reply = f'↪{l["reply_to"]} ' if l['reply_to'] else ''
        out.append(f'{l["sid"]} {d:%H:%M} {l["sender"]}：{reply}{text}')
    return '\n'.join(out)
