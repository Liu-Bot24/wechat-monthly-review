"""Which chats a run reads.

The choice is kept with the user's configuration (config.selection_path()), not in the repository, because it names contacts.
Rules, strongest first:
  1. chats the archive itself excludes are never read;
  2. a chat excluded by hand is skipped;
  3. a chat in a skipped category is skipped unless it was included by hand;
  4. everything else is read.
Skipped chats are dropped before extraction, so nothing from them reaches a model.
"""
import json
from pathlib import Path
from .archive import ancestors, within

class Selection:
    def __init__(self, path, default_skip_categories):
        self.path = Path(path)
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            data = {'skip_categories': list(default_skip_categories), 'exclude': {}, 'include': {}}
        self.skip_categories = list(data.get('skip_categories', []))
        self.exclude = dict(data.get('exclude', {}))     # username -> name when it was chosen, for reading the file
        self.include = dict(data.get('include', {}))

    @classmethod
    def of(cls, data):
        """A selection held elsewhere (a 专题's own), not read from or saved to a file."""
        sel = cls.__new__(cls)
        sel.path = None
        sel.skip_categories = list(data.get('skip_categories', []))
        sel.exclude, sel.include = dict(data.get('exclude', {})), dict(data.get('include', {}))
        return sel

    @property
    def data(self):
        return {'skip_categories': sorted(self.skip_categories), 'exclude': dict(sorted(self.exclude.items())),
                'include': dict(sorted(self.include.items()))}

    def save(self):
        """Write the choice; the version it replaces is kept as .bak."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.with_suffix('.bak').write_bytes(self.path.read_bytes())
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)

    def category_skipped(self, category):
        # An entry names a folder at any depth and covers everything below it.
        return any(within(category, k) for k in self.skip_categories)

    def reason(self, chat, category, archive_excluded=False):
        """Why this chat is skipped, or None when it is read."""
        if archive_excluded:
            return '归档策略排除'
        if chat in self.exclude:
            return '手动排除'
        if self.category_skipped(category) and chat not in self.include:
            return '整类跳过：' + next(k for k in ancestors(category) if k in self.skip_categories)
        return None

    def set_chat(self, chat, name, choice):
        """choice: 'exclude', 'include' or 'clear'. A chat is in at most one of the two lists."""
        self.exclude.pop(chat, None)
        self.include.pop(chat, None)
        if choice == 'exclude':
            self.exclude[chat] = name
        elif choice == 'include':
            self.include[chat] = name

    def set_category(self, category, skipped):
        if skipped and category not in self.skip_categories:
            self.skip_categories.append(category)
        elif not skipped and category in self.skip_categories:
            self.skip_categories.remove(category)


def resolve(archive, targets):
    """Map each target (WeChat ID, exact name, or a unique part of a name) to a chat.

    Returns (found, problems); found is [(chat, name)] and problems lists targets that matched nothing or several chats.
    """
    found, problems = {}, []
    chats = list(archive.folders)
    for t in targets:
        if t in archive.folders:
            found[t] = archive.names.get(t, t)
            continue
        exact = [c for c in chats if archive.names.get(c) == t]
        part = exact or [c for c in chats if t.lower() in (archive.names.get(c) or '').lower()]
        if len(part) == 1:
            found[part[0]] = archive.names.get(part[0], part[0])
        elif not part:
            problems.append(f'"{t}" 没有匹配到任何会话')
        else:
            listed = '；'.join(f'{archive.names.get(c, c)}（{c}）' for c in part[:10])
            problems.append(f'"{t}" 匹配到 {len(part)} 个会话，请用微信号或完整名称：{listed}{" ……" if len(part) > 10 else ""}')
    return list(found.items()), problems


def overview(archive, selection, start, end):
    """One row per chat in the archive with its counts in [start, end) and whether a run would read it."""
    rows = []
    for chat in archive.folders:
        category = archive.category(chat)
        n, mine, last = archive.counts(chat, start, end)
        rows.append({'chat': chat, 'name': archive.names.get(chat, chat), 'category': category,
                     'folder': archive.folders.get(chat), 'group': archive.is_group(chat),
                     'messages': n or 0, 'mine': (n if chat == 'filehelper' else mine) or 0, 'last': last,
                     'skip': selection.reason(chat, category, chat in archive.excluded)})
    return rows
