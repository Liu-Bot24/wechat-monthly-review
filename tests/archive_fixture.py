"""Chat documents in the archive's Markdown format, for tests."""
from pathlib import Path

def chat_doc(name, month, chat, me, messages, members=None, title_tail=''):
    """messages: (anchor, 'YYYY-MM-DD HH:MM:SS', sender name, type, sender id, extra fields, body lines, attachments)."""
    out = [f'# {name} · {month}{title_tail}', '', f'会话：{chat}', f'我：{me}'] + ([f'群人数：{members}'] if members else []) + ['']
    for anchor, time, sender, typ, sid, extra, body, attach in messages:
        out += [f'<a id="{anchor}"></a>', f'### {time} · {sender} · {typ}', '', f'发送者：{sid}{extra}', '']
        out += [f'> {line}' if line else '>' for line in body] + ['']
        out += [f'- [附件]({a})' for a in attach] + ([''] if attach else [])
    return '\n'.join(out)

def write_chat(root, folder, *args, file=None, sub='聊天记录', **kw):
    """Writes one document into <root>/<folder>/<sub>/ and returns its path."""
    path = Path(root) / folder / sub / (file or f'{args[1]}.md')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(chat_doc(*args, **kw), encoding='utf-8')
    return path
