"""Read-only access to the chat archive through its Markdown alone. Never writes to the archive.

The archive is a folder of chat documents in the documented format: each chat has a folder; its Markdown sits one level
below it, one document per month, starting with
    # <chat name> · <YYYY-MM>[ · …]
    会话：<chat id>
    我：<my id>
    群人数：<n>          (groups only)
and holding messages
    <a id="msg-…"></a>
    ### <YYYY-MM-DD HH:MM[:SS]> · <sender name> · <type>
    发送者：<id>[ · 引用：<anchor>][ · 提到：<id,…>][ · <other field>：…]
    > body (Markdown-escaped)
    - [text](attachment, relative to the document)
Which folder, file names or tool made it does not matter. The folders above a chat's folder are its category.

A small index of the documents (per day counts, senders, voice clips) is kept in this program's cache and brought up to
date from the files' sizes and times; messages themselves are read from the Markdown when needed.
"""
import hashlib, html, json, os, re, sqlite3, threading, time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote

TZ = timezone(timedelta(hours=8))
INDEX_VERSION = '2'
_ANCHOR = re.compile(r'^<a id="(msg-[0-9A-Za-z]+)"></a>\r?\n### ([^\n]*?)\r?$', re.M)
_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?$')
_MONTH = re.compile(r'^\d{4}-\d{2}$')
_ATTACH = re.compile(r'^- \[[^\]\n]*\]\((?:<([^>\n]+)>|([^)\s]+))\)\s*$')
_UNESCAPE = re.compile(r'\\([!-/:-@\[-`{-~])')
_END = re.compile(r'^(?:#{1,2} |---\s*$)')
AUDIO = {'.mp3', '.m4a', '.aac', '.wav', '.ogg', '.oga', '.opus', '.flac', '.amr', '.silk'}
IMAGE = {'.png', '.jpg', '.jpeg', '.jfif', '.gif', '.webp', '.bmp', '.heic', '.avif', '.tif', '.tiff'}
VIDEO = {'.mp4', '.m4v', '.webm', '.mov', '.ogv', '.avi', '.mkv'}
_PROBE = 200_000

def month_bounds(month):
    start = datetime.strptime(month, '%Y-%m').replace(tzinfo=TZ)
    end = (start + timedelta(days=32)).replace(day=1)
    return int(start.timestamp()), int(end.timestamp())

def stamp(ts):
    return datetime.fromtimestamp(ts, TZ)

def ancestors(category):
    """'群聊/分组' -> ['群聊', '群聊/分组']"""
    parts = category.split('/')
    return ['/'.join(parts[:i]) for i in range(1, len(parts) + 1)]

def category_title(category):
    """'05_群聊/00_分组' -> '群聊 · 分组': folder names without leading ordering numbers."""
    return ' · '.join(re.sub(r'^[0-9]+_', '', p) for p in category.split('/'))

def within(category, key):
    """A category rule covers the folder itself and every folder below it."""
    return category == key or category.startswith(key + '/')

def location_problem(root):
    """Why the archive folder chosen in settings cannot be read, or None."""
    if not root:
        return '还没有设置归档目录：请在 设置 › 常规 中选择。'
    if not Path(root).is_dir():
        return f'归档目录不存在：{root}'
    return None

def media_kind(path):
    ext = os.path.splitext(path)[1].lower()
    return 'image' if ext in IMAGE else 'video' if ext in VIDEO else 'voice' if ext in AUDIO else 'file'

def media_id(rel):
    """An attachment's id in this program: its path inside the archive, safe for a URL."""
    return rel.encode('utf-8').hex()

def media_path(mid):
    try:
        return bytes.fromhex(mid).decode('utf-8')
    except ValueError:
        return None

def _ts(heading_time):
    m = _TIME.match(heading_time.strip())
    if not m:
        return None
    return int(datetime.strptime(f'{m[1]} {m[2]}:{m[3] or "00"}', '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ).timestamp())

def _plain(text):
    """Markdown text as it was before escaping: backslash escapes and HTML entities undone."""
    if '\\' not in text and '&' not in text:
        return text
    return html.unescape(_UNESCAPE.sub(r'\1', text))

def parse(text, doc=''):
    """One chat document: (header, messages). header: name, month, chat, me, members; None when it is not a chat document.
    Each message: anchor, doc, timestamp, name, type, sender, quote, mentions, fields, content (body, unescaped), media
    [{kind, path}] with paths relative to the document's folder, as written."""
    first = _ANCHOR.search(text, 0, _PROBE)
    if not first:
        return None, []
    head = text[:first.start()]
    header = {'name': '', 'month': '', 'chat': '', 'me': '', 'members': None}
    for line in head.split('\n'):
        line = line.rstrip('\r')
        if line.startswith('# ') and not header['name']:
            parts = line[2:].rstrip(' \t').split(' · ')       # names are kept as written, blank ones included
            k = next((i for i in range(len(parts) - 1, 0, -1) if _MONTH.match(parts[i])), None)
            header['name'] = ' · '.join(parts[:k]) if k else parts[0]
            header['month'] = parts[k] if k else ''
        elif line.startswith('会话：'):
            header['chat'] = line[3:].strip()
        elif line.startswith('我：'):
            header['me'] = line[2:].strip()
        elif line.startswith('群人数：') and line[4:].strip().isdigit():
            header['members'] = int(line[4:].strip())
    marks = list(_ANCHOR.finditer(text, first.start()))
    messages = []
    header['name'] = _plain(header['name'])
    for i, m in enumerate(marks):
        parts = _plain(m[2]).split(' · ')
        ts = _ts(parts[0]) if len(parts) >= 3 else None
        if ts is None:
            continue
        block = text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        sender, fields, body, media, state = '', {}, [], [], 'meta'
        for line in block.split('\n'):
            line = line.rstrip('\r')
            if _END.match(line):
                break
            if state == 'meta':
                if not line.strip():
                    continue
                state = 'body'
                if line.startswith('发送者：'):
                    sender, *rest = line[4:].split(' · ')
                    for f in rest:
                        k, sep, v = f.partition('：')
                        if sep:
                            fields[k.strip()] = v.strip()
                    continue
            if state == 'body':
                if line.startswith('>'):
                    body.append(line[2:] if line.startswith('> ') else line[1:])
                    continue
                if body or line.strip():
                    state = 'after'
            if state == 'after':
                a = _ATTACH.match(line)
                if a:
                    rel = unquote(a[1] or a[2])
                    if not re.match(r'^[a-z][a-z0-9+.-]*:', rel, re.I):
                        media.append({'kind': media_kind(rel), 'path': rel})
        mentions = [x.strip() for x in fields.get('提到', '').split(',') if x.strip()]
        sender = sender.strip()
        content = _plain('\n'.join(body))
        if sender and content.startswith(sender + ':\n'):
            content = content[len(sender) + 2:]     # group messages are often stored as "<sender id>:\n<text>"
        messages.append({'anchor': m[1], 'doc': doc, 'timestamp': ts, 'name': ' · '.join(parts[1:-1]), 'type': parts[-1].strip(),
                         'sender': sender, 'quote': fields.get('引用') or None, 'mentions': mentions, 'fields': fields,
                         'content': content, 'media': media})
    return header, messages


@contextmanager
def one_at_a_time(path):
    """A lock between processes (and threads): the window and the runs it starts share one index, and only one of them
    brings it up to date at a time; the others wait, then find it current."""
    f = open(path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.5)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == 'nt':
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        f.close()

class Archive:
    def __init__(self, root, reports=None, index=None, progress=None):
        """root: the archive folder chosen in settings. reports: the report folder, whose printed message numbers are kept.
        index: this program's index file (default: in its cache folder)."""
        problem = location_problem(root)
        if problem:
            raise SystemExit(problem)
        self.root = Path(root)
        if index is None:
            from . import config
            index = config.HOME / 'cache' / 'archive-index.sqlite'
        Path(index).parent.mkdir(parents=True, exist_ok=True)
        self._lockfile = str(index) + '.lock'
        # The window's server shares one connection across its worker threads under its own lock.
        self.db = sqlite3.connect(str(index), check_same_thread=False, timeout=60)
        self.db.row_factory = sqlite3.Row
        try:
            # Switching to WAL and laying out the tables do not wait for another opener doing the same.
            with one_at_a_time(self._lockfile):
                self.db.execute('PRAGMA journal_mode=WAL')
                self._create()
        except BaseException:
            self.db.close()
            raise
        self.excluded = set()          # the archive decides what it holds; nothing is excluded here
        self._files = {}
        self._lock = threading.RLock()
        self.refresh(progress)
        if reports:
            self.adopt_numbers(reports)

    def close(self):
        self.db.close()

    # ---------- index ----------
    def _create(self):
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS docs(path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, chat TEXT, name TEXT, month TEXT, me TEXT,
                members INTEGER, n INTEGER, first INTEGER, last INTEGER, days TEXT, senders TEXT);
            CREATE INDEX IF NOT EXISTS docs_chat ON docs(chat, first);
            CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY, name TEXT, ts INTEGER);
            CREATE TABLE IF NOT EXISTS voices(doc TEXT, anchor TEXT, path TEXT, chat TEXT, ts INTEGER, sender TEXT, size INTEGER,
                PRIMARY KEY(doc, anchor, path));
            CREATE INDEX IF NOT EXISTS voices_ts ON voices(ts);
            CREATE INDEX IF NOT EXISTS voices_size ON voices(size);
            CREATE TABLE IF NOT EXISTS refs(id INTEGER PRIMARY KEY, anchor TEXT UNIQUE, doc TEXT);''')
        root = str(Path(self.root).resolve()) if hasattr(self, 'root') else ''
        have = dict(self.db.execute('SELECT key, value FROM meta').fetchall())
        if have.get('root') != root or have.get('version') != INDEX_VERSION:
            # Another archive folder (or index layout): what was indexed belongs to something else. Message numbers stay.
            self.db.executescript('DELETE FROM docs; DELETE FROM people; DELETE FROM voices;')
            self.db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)', [('root', root), ('version', INDEX_VERSION)])
            self.db.commit()

    def _walk(self):
        found = {}
        def walk(path, rel):
            try:
                entries = list(os.scandir(path))
            except OSError:
                return
            for e in entries:
                if e.name.startswith('.'):
                    continue
                sub = f'{rel}/{e.name}' if rel else e.name
                try:
                    if e.is_dir(follow_symlinks=False):
                        walk(e.path, sub)
                    elif e.name.lower().endswith('.md'):
                        st = e.stat()
                        found[sub] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    continue
        walk(self.root, '')
        return found

    def refresh(self, progress=None):
        """Bring the index up to date with the archive folder: documents added, changed or removed since the last look."""
        with self._lock, one_at_a_time(self._lockfile):
            on_disk = self._walk()
            known = {r[0]: (r[1], r[2]) for r in self.db.execute('SELECT path, mtime, size FROM docs')}
            gone = [p for p in known if p not in on_disk]
            todo = [p for p, s in on_disk.items() if known.get(p) != s]
            for i, rel in enumerate(todo):
                self._index_doc(rel, on_disk[rel])
                if progress and i % 200 == 0:
                    progress(i, len(todo))
                if i % 500 == 499:
                    self.db.commit()
                    time.sleep(0.15)     # a gap for another process's write (a run numbering messages) to get in
                                         # instead of waiting out its timeout behind back-to-back batches
            for rel in gone:
                self.db.execute('DELETE FROM docs WHERE path=?', (rel,))
                self.db.execute('DELETE FROM voices WHERE doc=?', (rel,))
            self.db.commit()
            self._load()

    def _index_doc(self, rel, stat):
        self.db.execute('DELETE FROM voices WHERE doc=?', (rel,))
        try:
            text = (self.root / rel).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return
        header, msgs = parse(text, rel)
        if not header or not header['chat'] or not msgs:
            self.db.execute('INSERT OR REPLACE INTO docs(path, mtime, size) VALUES(?,?,?)', (rel, *stat))   # not a chat document
            return
        me = header['me']
        days = {}
        for m in msgs:
            d = days.setdefault(stamp(m['timestamp']).strftime('%Y-%m-%d'), [0, 0])
            d[0] += 1
            d[1] += m['sender'] == me and bool(me)
        folder = str(Path(rel).parent.as_posix())
        self.db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (rel, *stat, header['chat'], header['name'], header['month'], me, header['members'], len(msgs),
                         msgs[0]['timestamp'], max(m['timestamp'] for m in msgs), json.dumps(days), json.dumps(sorted({m['sender'] for m in msgs if m['sender']}))))
        latest = {}
        for m in msgs:
            if m['sender'] and m['name']:
                latest[m['sender']] = (m['name'], m['timestamp'])
        self.db.executemany('''INSERT INTO people VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, ts=excluded.ts
                               WHERE excluded.ts >= people.ts''', [(k, v[0], v[1]) for k, v in latest.items()])
        rows = []
        for m in msgs:
            for f in m['media']:
                if f['kind'] == 'voice':
                    path = (Path(folder) / f['path']).as_posix()
                    path = os.path.normpath(path).replace('\\', '/')
                    try:
                        size = (self.root / path).stat().st_size
                    except OSError:
                        size = None
                    rows.append((rel, m['anchor'], path, header['chat'], m['timestamp'], m['sender'], size))
        self.db.executemany('INSERT OR REPLACE INTO voices VALUES(?,?,?,?,?,?,?)', rows)

    def _load(self):
        """Chats, their folders and names from the indexed documents."""
        docs = self.db.execute('SELECT path, chat, name, me, members, n, first, last, days, senders FROM docs WHERE chat IS NOT NULL ORDER BY first').fetchall()
        self.folders, self.names, self._docs, self._groups = {}, {}, {}, set()
        mine = Counter()
        for d in docs:
            chat = d['chat']
            self._docs.setdefault(chat, []).append(d)
            # The chat's folder is the one above the folder its Markdown is in; the newest document decides.
            self.folders[chat] = Path(d['path']).parent.parent.as_posix().strip('.') or Path(d['path']).parent.as_posix()
            if d['name']:
                self.names[chat] = d['name']
            if d['members'] is not None:
                self._groups.add(chat)
            if d['me']:
                mine[d['me']] += d['n']
        self.self_id = mine.most_common(1)[0][0] if mine else ''
        self._chat_dirs = set(self.folders.values())
        self._days = {}
        self._people = {}

    # ---------- chats ----------
    def path(self, rel):
        """A path inside the archive folder, from one relative to it."""
        return self.root / (rel or '')

    def is_group(self, chat):
        return chat in self._groups or chat.endswith('@chatroom')

    def category(self, chat):
        """The folders above the chat's own folder, at whatever depth; a chat kept inside another chat's folder takes that chat's category."""
        folder = self.folders.get(chat) or '?'
        parts = folder.split('/')[:-1]
        while len(parts) > 1 and '/'.join(parts) in self._chat_dirs:
            parts.pop()
        return '/'.join(parts) or folder

    def categories(self):
        """Every category that holds a chat, with the folders above it. Only chats found in documents count."""
        return sorted({a for c in self.folders for a in ancestors(self.category(c))})

    def person(self, username):
        if username not in self._people:
            with self._lock:
                row = self.db.execute('SELECT name FROM people WHERE id=?', (username,)).fetchone()
            self._people[username] = row[0] if row and row[0] else username
        return self._people[username]

    def find_people(self, name, limit=20, exact=False):
        """[(id, name)] of senders whose latest name is name (exact) or contains it."""
        with self._lock:
            if exact:
                return self.db.execute('SELECT id, name FROM people WHERE name=? LIMIT ?', (name, limit)).fetchall()
            return self.db.execute("SELECT id, name FROM people WHERE name LIKE ? ESCAPE '\\' LIMIT ?",
                                   ('%' + re.sub(r'([%_\\])', r'\\\1', name) + '%', limit)).fetchall()

    def earliest(self):
        """The oldest message time after 2000 (earlier ones are broken clocks), or None."""
        return min((d['first'] for ds in self._docs.values() for d in ds if d['first'] > 946684800), default=None)

    def latest(self):
        """Just after the newest message in the archive (0 when it is empty)."""
        last = max((d['last'] for ds in self._docs.values() for d in ds), default=None)
        return (last or 0) + 1

    def _day_counts(self, chat):
        if chat not in self._days:
            out = {}
            for d in self._docs.get(chat, []):
                for day, (n, mine) in json.loads(d['days'] or '{}').items():
                    o = out.setdefault(day, [0, 0])
                    o[0] += n
                    o[1] += mine
            self._days[chat] = out
        return self._days[chat]

    def counts(self, chat, start, end):
        """(messages, mine, newest timestamp) of the chat in [start, end), counted by whole days."""
        lo, hi = stamp(start).strftime('%Y-%m-%d'), stamp(end - 1).strftime('%Y-%m-%d')
        n = mine = 0
        for day, (a, b) in self._day_counts(chat).items():
            if lo <= day <= hi:
                n += a
                mine += b
        last = max((d['last'] for d in self._docs.get(chat, []) if d['first'] < end and d['last'] >= start), default=None)
        if last is not None and last >= end:
            last = max((m['timestamp'] for m in self.messages(chat, start, end)), default=None)
        return n, mine, last

    def month_counts(self, chat, since):
        """{'YYYY-MM': (messages, mine)} from since on, and the chat's newest message time."""
        lo = stamp(since).strftime('%Y-%m-%d')
        out = {}
        for day, (a, b) in self._day_counts(chat).items():
            if day >= lo:
                o = out.setdefault(day[:7], [0, 0])
                o[0] += a
                o[1] += b
        last = max((d['last'] for d in self._docs.get(chat, [])), default=None)
        return {k: tuple(v) for k, v in out.items()}, last

    def active_chats(self, start, end):
        """Chats with at least one message in [start, end): [(chat, count)]."""
        found = []
        for chat in self.folders:
            n = self.counts(chat, start, end)[0]
            if n:
                found.append((chat, n))
        return found

    def month_messages(self, start, end):
        return sum(n for _, n in self.active_chats(start, end))

    def rewritten_since(self, start, end, since):
        """Chats with a document covering some of [start, end) that was written after `since` (epoch seconds)."""
        rows = self.db.execute('SELECT DISTINCT chat FROM docs WHERE chat IS NOT NULL AND mtime > ? AND first < ? AND last >= ?',
                               (int(since * 1e9), end, start))
        return {r[0] for r in rows}

    def has_sender(self, chat, start, end, senders):
        """True when one of the chat's documents in [start, end) has a message from one of senders."""
        return any(d['first'] < end and d['last'] >= start and set(json.loads(d['senders'] or '[]')) & senders
                   for d in self._docs.get(chat, []))

    # ---------- messages ----------
    def _read(self, rel):
        """Parsed messages of one document, cached by the file's size and time."""
        try:
            st = (self.root / rel).stat()
        except OSError:
            return []
        key = (st.st_mtime_ns, st.st_size)
        hit = self._files.get(rel)
        if hit and hit[0] == key:
            return hit[1]
        try:
            text = (self.root / rel).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return []
        header, msgs = parse(text, rel)
        folder = Path(rel).parent
        for m in msgs:
            m['chat'] = header['chat']
            m['members'] = header['members']
            for f in m['media']:
                f['path'] = os.path.normpath((folder / f['path']).as_posix()).replace('\\', '/')
        if len(self._files) > 64:
            self._files.pop(next(iter(self._files)))
        self._files[rel] = (key, msgs)
        return msgs

    def messages(self, chat, start, end):
        """The chat's messages in [start, end), oldest first."""
        out = []
        for d in self._docs.get(chat, []):
            if d['first'] < end and d['last'] >= start:
                out += [m for m in self._read(d['path']) if start <= m['timestamp'] < end]
        out.sort(key=lambda m: m['timestamp'])
        return out

    def message(self, doc, anchor):
        return next((m for m in self._read(doc) if m['anchor'] == anchor), None)

    def sender_of(self, chat, anchor, before=None, months=6):
        """Sender id of a message given by its anchor, looked for in the chat's documents up to `months` back from before."""
        docs = [d for d in self._docs.get(chat, []) if before is None or d['first'] <= before]
        for d in reversed(docs[-months:]):
            hit = self.message(d['path'], anchor)
            if hit:
                return hit['sender']
        return None

    def around(self, doc, anchor, n):
        """(messages, index of the target): up to n messages before and after it in the same chat."""
        msgs = self._read(doc)
        i = next((k for k, m in enumerate(msgs) if m['anchor'] == anchor), None)
        if i is None:
            return [], None
        chat = msgs[i]['chat']
        docs = [d['path'] for d in self._docs.get(chat, [])]
        k = docs.index(doc) if doc in docs else -1
        before, after = msgs[:i], msgs[i + 1:]
        j = k
        while len(before) < n and j > 0:
            j -= 1
            before = self._read(docs[j]) + before
        j = k
        while len(after) < n and 0 <= j < len(docs) - 1:
            j += 1
            after = after + self._read(docs[j])
        shown = before[-n:] + [msgs[i]] + after[:n]
        return shown, min(n, len(before))

    def recent(self, chat, n, skip_types=('系统消息',)):
        out = []
        for d in reversed(self._docs.get(chat, [])):
            out = [m for m in self._read(d['path']) if m['type'] not in skip_types] + out
            if len(out) >= n:
                break
        return out[-n:]

    def readable_body(self, content, typ=None, sender=None):
        """The body as the archive wrote it; Markdown escaping is already undone."""
        return content or ''

    def media_of(self, doc, anchor):
        """Attachments of one message: [{kind, path}], path relative to the archive folder."""
        m = self.message(doc, anchor)
        return m['media'] if m else []

    def voices(self, start, end, chats=None):
        """Voice clips in [start, end): [{chat, timestamp, sender, anchor, doc, main_path, id}], oldest first."""
        with self._lock:
            rows = self.db.execute('SELECT * FROM voices WHERE ts>=? AND ts<? ORDER BY ts', (start, end)).fetchall()
        return [{'chat': r['chat'], 'timestamp': r['ts'], 'sender': r['sender'], 'anchor': r['anchor'], 'doc': r['doc'],
                 'main_path': r['path'], 'id': media_id(r['path'])} for r in rows if chats is None or r['chat'] in chats]

    def voice_copies(self, rel):
        """Every file in the archive holding the same clip as rel (the archive may keep a copy in each chat)."""
        target = self.root / rel
        try:
            size = target.stat().st_size
        except OSError:
            return []
        with self._lock:
            paths = [r[0] for r in self.db.execute('SELECT DISTINCT path FROM voices WHERE size=?', (size,))]
        want, out = None, []
        for p in paths:
            f = self.root / p
            if p != rel:
                if want is None:
                    want = hashlib.sha256(target.read_bytes()).digest()
                try:
                    if hashlib.sha256(f.read_bytes()).digest() != want:
                        continue
                except OSError:
                    continue
            if f.exists() and f not in out:
                out.append(f)
        return out or [target]

    # ---------- message numbers ----------
    def ids(self, refs):
        """{anchor: number} for [(doc, anchor)]: the number a report shows for the message, the same every time."""
        with self._lock:
            self.db.executemany('INSERT OR IGNORE INTO refs(anchor, doc) VALUES(?,?)', [(a, d) for d, a in refs])
            self.db.executemany('UPDATE refs SET doc=? WHERE anchor=? AND doc IS NOT ?', [(d, a, d) for d, a in refs])
            self.db.commit()
            out = {}
            anchors = [a for _, a in refs]
            for i in range(0, len(anchors), 500):
                part = anchors[i:i + 500]
                out.update(self.db.execute(f'SELECT anchor, id FROM refs WHERE anchor IN ({",".join("?" * len(part))})', part).fetchall())
        return out

    def ref(self, number):
        """(doc, anchor) behind a message number, or None."""
        with self._lock:
            row = self.db.execute('SELECT doc, anchor FROM refs WHERE id=?', (number,)).fetchone()
        if not row:
            return None
        doc, anchor = row
        if not (self.root / doc).is_file():
            doc = self._find(anchor) or doc
        return doc, anchor

    def _find(self, anchor):
        """The document now holding an anchor whose recorded document is gone (a folder moved since)."""
        needle = f'<a id="{anchor}"></a>'
        for ds in self._docs.values():
            for d in ds:
                try:
                    if needle in (self.root / d['path']).read_text(encoding='utf-8', errors='replace'):
                        with self._lock:
                            self.db.execute('UPDATE refs SET doc=? WHERE anchor=?', (d['path'], anchor))
                            self.db.commit()
                        return d['path']
                except OSError:
                    continue
        return None

    def adopt_numbers(self, reports_root):
        """Numbers already printed in reports keep meaning the same messages: read them from the reports' own links once."""
        reports_root = Path(reports_root)
        key = 'adopted:' + str(reports_root.resolve())
        with self._lock:
            if self.db.execute('SELECT 1 FROM meta WHERE key=?', (key,)).fetchone() or not reports_root.is_dir():
                return 0
        link = re.compile(r'\[#(\d+)\]\(<?([^<>()\s]+?\.md)#(msg-[0-9A-Za-z]+)>?\)')
        rows = {}
        for f in reports_root.rglob('*.md'):
            try:
                text = f.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            for n, target, anchor in link.findall(text):
                t = unquote(target)
                p = Path(t[8:]) if t.startswith('file:///') else (f.parent / t)
                try:
                    rel = Path(os.path.normpath(p)).relative_to(Path(os.path.normpath(self.root))).as_posix()
                except ValueError:
                    continue
                rows.setdefault(int(n), (anchor, rel))
        with self._lock:
            self.db.executemany('INSERT OR IGNORE INTO refs(id, anchor, doc) VALUES(?,?,?)', [(n, a, d) for n, (a, d) in rows.items()])
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?, ?)', (key, str(len(rows))))
            self.db.commit()
        return len(rows)

    def link(self, document_path, anchor, base=None):
        """Link to the message's place in the archive's Markdown: relative to base when possible, file:// otherwise."""
        target = self.path(document_path)
        if base is not None:
            try:
                return '<' + Path(os.path.relpath(target, base)).as_posix() + '#' + anchor + '>'
            except ValueError:   # report and archive on different drives
                pass
        return 'file:///' + quote(target.as_posix(), safe='/:') + '#' + anchor
