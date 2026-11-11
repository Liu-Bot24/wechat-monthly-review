import unittest
from pathlib import Path
from mr import config
from mr.selection import Selection
from mr.topic import Scope, blank, blockers, clean, clip, keyword_pattern, plain_text, search_digest, spans, _unit

def line(i, ts, text='x'):
    return {'sid': f'm{i}', 'ts': ts, 'text': text, 'in_month': True}

class SpecTest(unittest.TestCase):
    def test_clean_fills_and_refuses(self):
        s = clean({'topic': {'mode': 'text', 'keywords': [' 相机 ', '相机', '', '镜头']}, 'perspective': 'bogus'})
        self.assertEqual(s['topic']['keywords'], ['相机', '镜头'])
        self.assertEqual(s['perspective'], 'mine')
        self.assertIsNone(s['scope']['selection'])
        with self.assertRaises(config.ConfigError):
            clean({'range': {'from': '2026-10-31', 'to': '2026-10-01'}})
        with self.assertRaises(config.ConfigError):
            clean({'range': {'from': '2026/10/01', 'to': '2026-10-31'}})

    def test_blockers(self):
        self.assertEqual(len(blockers(blank())), 2)                       # no time, no scope
        s = clean({'range': {'from': '2026-10-01', 'to': '2026-10-31'}, 'scope': {'all': True}, 'topic': {'mode': 'text'}})
        self.assertEqual(blockers(s), ['按关键词查找时至少要有一个关键词'])
        s['topic']['keywords'] = ['相机']
        self.assertEqual(blockers(s), [])

    def test_search_digest_ignores_how_it_is_written(self):
        a = clean({'range': {'from': '2026-10-01', 'to': '2026-10-31'}, 'scope': {'all': True}})
        b = clean({**a, 'template': 'timeline', 'title': '别的标题', 'exclude_chats': ['x']})
        c = clean({**a, 'topic': {'mode': 'text', 'keywords': ['相机']}})
        self.assertEqual(search_digest(a), search_digest(b))
        self.assertNotEqual(search_digest(a), search_digest(c))

    def test_unit(self):
        self.assertEqual(_unit('2026-10-05', '2026-10-05'), 'day')
        self.assertEqual(_unit('2026-02-01', '2026-02-28'), 'month')
        self.assertEqual(_unit('2025-01-01', '2025-12-31'), 'year')
        self.assertEqual(_unit('2026-10-01', '2026-10-30'), 'custom')

class WindowTest(unittest.TestCase):
    def test_spans_stay_inside_a_conversation_and_merge(self):
        # two conversations: lines 0-9 a minute apart, then a three-hour gap, then 10-19
        lines = [line(i, 60 * i) for i in range(10)] + [line(i, 20000 + 60 * i) for i in range(10, 20)]
        self.assertEqual(spans(lines, [8], 3), [[5, 10]])                 # does not run into the next conversation
        self.assertEqual(spans(lines, [2, 5], 2), [[0, 8]])                # touching windows merge
        self.assertEqual(spans(lines, [2, 15], 1), [[1, 4], [14, 17]])

    def test_clip(self):
        self.assertEqual(clip([[0, 10], [20, 30]], [[5, 25]]), [[5, 10], [20, 25]])
        self.assertEqual(clip([[0, 3]], [[5, 9]]), [])

    def test_keywords_match_case_and_longest_first(self):
        p = keyword_pattern(['GPT', 'GPT-6'])
        self.assertEqual(p.search('试了 gpt-6 的新版').group(0), 'gpt-6')
        self.assertIsNone(p.search('今天天气不错'))

class FakeArchive:
    def __init__(self, folders, excluded=()):
        self.folders, self.excluded = folders, set(excluded)
        self.names = {c: f.rsplit('/', 1)[-1] for c, f in folders.items()}
    def category(self, chat):
        return self.folders[chat].rsplit('/', 1)[0]
    def categories(self):
        from mr.archive import ancestors
        return sorted({a for c in self.folders for a in ancestors(self.category(c))})
    def person(self, username):
        return username

ARCHIVE = {'zhang': '01_私聊/张三', 'li': '01_私聊/李四', 'wang': '01_私聊/王五',
           'ai@chatroom': '05_群/00_AI/AI群', 'ai2@chatroom': '05_群/00_AI/AI二群', 'chat@chatroom': '05_群/闲聊群', 'low@chatroom': '06_低质/水群'}

def own(selection, people=None, follow=False):
    return clean({'scope': {'follow_monthly': follow, 'selection': selection,
                            'people': {'on': people is not None, **(people or {})}}})

class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.a = FakeArchive(ARCHIVE)
        self.monthly = Selection('nonexistent.json', ['06_低质'])

    def reads(self, sc):
        return {c for c in ARCHIVE if sc.reason(c, self.a.category(c)) is None}

    def test_nothing_chosen_reads_nothing(self):
        spec = own(None)
        sc = Scope(spec, self.monthly)
        self.assertEqual(self.reads(sc), set())
        self.assertEqual(blockers(spec, sc.reach(self.a))[-1], '在读取范围里选择要读的会话')

    def test_follow_monthly_uses_its_rules(self):
        sc = Scope(own({'skip_categories': ['01_私聊']}, follow=True), self.monthly)   # its own selection is kept but unused
        self.assertEqual(sc.reason('low@chatroom', '06_低质'), '整类跳过：06_低质')
        self.assertIsNone(sc.reason('zhang', '01_私聊'))
        self.assertEqual(sc.describe(self.a), '沿用月报的读取范围')

    def test_own_selection(self):
        sc = Scope(own({'skip_categories': ['01_私聊', '06_低质'], 'include': {'zhang': '张三'}}), self.monthly)
        self.assertEqual(self.reads(sc), {'zhang', 'ai@chatroom', 'ai2@chatroom', 'chat@chatroom'})
        self.assertEqual(sc.reason('li', '01_私聊'), '不在范围内')
        self.assertEqual(sc.describe(self.a), '分类：群；会话：张三')
        self.assertEqual(Scope(own({'skip_categories': []}), self.monthly).describe(self.a), '全部会话')

    def test_people_in_every_group(self):
        spec = own({'skip_categories': ['01_私聊', '05_群', '06_低质'], 'include': {'zhang': '张三', 'ai@chatroom': 'AI群'}},
                   {'add': [{'id': 'wxid_x', 'name': '某人'}], 'where': 'all'})
        sc = Scope(spec, self.monthly).bind(self.a)
        self.assertEqual(sc.people, {'zhang', 'wxid_x'})
        self.assertTrue(sc.searches('chat@chatroom', '05_群'))         # not chosen: looked in for their lines
        self.assertFalse(sc.searches('ai@chatroom', '05_群/00_AI'))     # chosen: read whole anyway
        self.assertIsNone(sc.reason('ai@chatroom', '05_群/00_AI'))
        self.assertEqual(sc.reason('chat@chatroom', '05_群'), '不在范围内')
        sc.via_people.add('chat@chatroom')
        self.assertIsNone(sc.reason('chat@chatroom', '05_群'))
        self.assertIn('另读 张三、某人 在所有群聊里的发言', sc.describe(self.a))

    def test_people_only_in_chosen_groups(self):
        spec = own({'skip_categories': ['01_私聊', '06_低质'], 'include': {'zhang': '张三'}, 'exclude': {'chat@chatroom': '闲聊群'}},
                   {'remove': [], 'where': 'picked', 'skip_private': True})
        sc = Scope(spec, self.monthly)
        reach = sc.reach(self.a)
        self.assertEqual((reach['people'], reach['groups']), (1, 2))
        self.assertEqual(blockers(spec, reach), [b for b in blockers(spec, reach) if b == '选择时间'])
        self.assertTrue(sc.searches('ai@chatroom', '05_群/00_AI'))
        self.assertFalse(sc.searches('chat@chatroom', '05_群'))
        self.assertEqual(sc.reason('ai@chatroom', '05_群/00_AI'), '不在范围内')   # read only where they spoke, for their lines
        self.assertEqual(sc.reason('zhang', '01_私聊'), '不在范围内')             # their private chat only picks them
        self.assertIn('勾选的群聊只读 张三 的发言（不读他们的私聊）', sc.describe(self.a))

    def test_people_block_needs_people_and_groups(self):
        spec = own({'skip_categories': ['01_私聊', '05_群', '06_低质']}, {'where': 'all'})
        self.assertIn('给"联系人在群聊里的发言"选择对象', blockers(spec, Scope(spec, self.monthly).reach(self.a)))
        spec = own({'skip_categories': ['05_群', '06_低质']}, {'where': 'picked'})
        self.assertIn('只在勾选的群聊里找发言时，至少勾选一个群聊', blockers(spec, Scope(spec, self.monthly).reach(self.a)))

    def test_named_lists_become_a_selection(self):
        from mr.topic import from_lists, upgrade
        old = clean({'scope': {'categories': ['05_群/00_AI'], 'chats': [{'id': 'low@chatroom', 'name': '水群'}],
                               'people': [{'id': 'li', 'name': '李四'}], 'people_in_groups': True, 'use_selection': True}})
        self.assertIn('legacy', old['scope'])
        self.assertTrue(upgrade(old, self.a, Selection('nonexistent.json', ['05_群/00_AI/旧'])))
        sel = old['scope']['selection']
        self.assertEqual(sel['skip_categories'], ['01_私聊', '05_群/00_AI/旧', '06_低质'])   # the monthly skip inside a chosen category stays
        self.assertEqual(sel['exclude'], {'chat@chatroom': '闲聊群'})              # sits directly in the folder above the chosen one
        self.assertEqual(set(sel['include']), {'low@chatroom', 'li'})
        self.assertEqual(old['scope']['people'], {'on': True, 'add': [], 'remove': [], 'where': 'all', 'skip_private': False})
        sc = Scope(old, self.monthly)
        self.assertEqual(self.reads(sc), {'li', 'ai@chatroom', 'ai2@chatroom', 'low@chatroom'})
        everything = from_lists(self.a, self.monthly, clean({'scope': {'all': True}})['scope']['legacy'])
        self.assertTrue(everything['follow_monthly'])

    def test_converting_keeps_results_current(self):
        import json, tempfile
        from mr.topic import migrate
        lists = {'all': False, 'chats': [{'id': 'ai@chatroom', 'name': 'AI群'}], 'people': [], 'categories': [],
                 'people_in_groups': True, 'use_selection': True}
        spec = clean({'range': {'unit': 'custom', 'from': '2026-10-01', 'to': '2026-10-31'}, 'scope': lists})
        made = config.digest({**{k: spec[k] for k in ('range', 'topic', 'reader')}, 'scope': lists})   # as worked out before
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'plan.json').write_text(json.dumps({'search': made}), encoding='utf-8')
            (Path(d) / 'status.json').write_text(json.dumps({'report_search': 'other'}), encoding='utf-8')
            spec = migrate(d, spec, self.a, self.monthly)
            self.assertNotIn('legacy', spec['scope'])
            self.assertEqual(json.loads((Path(d) / 'plan.json').read_text(encoding='utf-8'))['search'], search_digest(spec))
            self.assertEqual(json.loads((Path(d) / 'status.json').read_text(encoding='utf-8'))['report_search'], 'other')
            self.assertIs(migrate(d, spec, self.a, self.monthly), spec)   # already converted: left alone

    def test_search_digest_follows_what_is_read(self):
        a = own({'skip_categories': ['01_私聊']}, follow=True)
        b = own({'skip_categories': []}, follow=True)
        self.assertEqual(search_digest(a), search_digest(b))
        a['scope']['follow_monthly'] = b['scope']['follow_monthly'] = False
        self.assertNotEqual(search_digest(a), search_digest(b))

class PlainTextTest(unittest.TestCase):
    def test_strips_marks_and_local_links(self):
        md = ('## 结论\n\n- **定了** 11 月 9 日拍摄 [#123](<../../归档/a.md#msg-1>) [#124](<x.md#msg-2>)。\n'
              '- [ ] 你：发合同\n> 引用\n\n| 日期 | 事 |\n| --- | --- |\n| 10-01 | 开会 |\n'
              '看 [这篇](https://example.com/a)')
        out = plain_text(md)
        self.assertIn('【结论】', out)
        self.assertIn('· 定了 11 月 9 日拍摄。', out)
        self.assertIn('☐ 你：发合同', out)
        self.assertIn('10-01  开会', out)
        self.assertIn('看 这篇', out)
        for gone in ('#123', '**', '](', '---', '> '):
            self.assertNotIn(gone, out)

if __name__ == '__main__':
    unittest.main()


class DraftTest(unittest.TestCase):
    def _apply(self, scope):
        from unittest import mock
        from mr import topic
        answer = {'range': {}, 'scope': scope, 'topic': {}}
        found = [{'id': 'ai@chatroom', 'name': 'AI群', 'category': '05_群/00_AI'}]
        a = FakeArchive(ARCHIVE)
        with mock.patch.object(topic, 'find_chats', return_value=found), mock.patch.object(topic, 'find_people', return_value=[{'id': 'zhang', 'name': '张三'}]):
            return topic.apply_draft(a, blank(), answer, '要求', a.categories())[0]['scope']

    def test_named_chats_become_its_own_selection(self):
        sc = self._apply({'all': True, 'chats': ['AI群']})
        self.assertFalse(sc['follow_monthly'])
        self.assertEqual(sc['selection']['include'], {'ai@chatroom': 'AI群'})

    def test_all_follows_the_monthly_selection(self):
        self.assertTrue(self._apply({'all': True})['follow_monthly'])

    def test_a_person_in_named_groups(self):
        sc = self._apply({'chats': ['AI群'], 'people': ['张三'], 'people_where': 'picked', 'people_only': True})
        self.assertEqual(sc['selection']['include'], {'ai@chatroom': 'AI群'})
        self.assertEqual(sc['people']['add'], [{'id': 'zhang', 'name': '张三'}])
        self.assertEqual((sc['people']['where'], sc['people']['skip_private']), ('picked', True))


class CategoryTest(unittest.TestCase):
    """Categories are the folders between the numbered top level and each chat's own folder; nothing inside a chat folder counts."""

    @staticmethod
    def _chat(root, folder, chat):
        from archive_fixture import write_chat
        (Path(root) / folder / '图片').mkdir(parents=True, exist_ok=True)
        write_chat(root, folder, folder.rsplit('/', 1)[-1], '2026-10', chat, 'me',
                   [(f'msg-{chat}', '2026-10-01 09:00:00', '张三', '文本', 'zs', '', ['在'], [])])

    @staticmethod
    def _archive(root, stack):
        from mr.archive import Archive
        a = Archive(root, index=Path(root).parent / (Path(root).name + '.index.sqlite'))
        stack.callback(a.close)      # closed before the folder is removed
        return a

    def test_depth_nesting_and_only_chat_documents(self):
        import tempfile
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp) / 'a'
            folders = {'p1': '01_私聊/张三', 'g1': '05_群/00_AI/群一', 'g2': '05_群/00_AI/深/群二', 'old': '07_其它/群三/旧群记录', 'g3': '07_其它/群三'}
            for c, f in folders.items():
                self._chat(root, f, c)
            (root / '08_新分类' / '空分组').mkdir(parents=True)
            (root / '10_别的软件' / 'src').mkdir(parents=True)
            (root / '10_别的软件' / 'README.md').write_text('# 说明\n', encoding='utf-8')
            a = self._archive(root, stack)
            self.assertEqual(a.folders, folders)
            self.assertEqual(a.category('g1'), '05_群/00_AI')
            self.assertEqual(a.category('g2'), '05_群/00_AI/深')
            self.assertEqual(a.category('old'), '07_其它')                       # kept inside another chat's folder
            self.assertEqual(a.categories(), ['01_私聊', '05_群', '05_群/00_AI', '05_群/00_AI/深', '07_其它'])

    def test_follows_folders_moved_by_hand(self):
        import tempfile
        from contextlib import ExitStack
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp) / 'a'
            self._chat(root, '03_工作/分组甲/群一', 'b1')
            self._chat(root, '03_工作/分组甲/群二', 'b2')
            self._chat(root, '03_工作/分组甲/群二/旧群记录', 'old')
            a = self._archive(root, stack)
            (root / '03_工作/分组甲/子组').mkdir()
            (root / '03_工作/分组甲/群一').rename(root / '03_工作/分组甲/子组/群一')
            (root / '03_工作/分组甲/群二').rename(root / '03_工作/分组甲/子组/群二改名')
            a.refresh()
            self.assertEqual(a.folders['b1'], '03_工作/分组甲/子组/群一')
            self.assertEqual(a.folders['old'], '03_工作/分组甲/子组/群二改名/旧群记录')
            self.assertEqual(a.category('b1'), '03_工作/分组甲/子组')
            self.assertEqual(a.category('old'), '03_工作/分组甲/子组')
            self.assertIn('03_工作/分组甲/子组', a.categories())

    def test_rules_cover_sub_groups(self):
        from mr.archive import category_title, within
        self.assertTrue(within('05_群/00_AI/深', '05_群'))
        self.assertFalse(within('05_群聊2', '05_群'))
        self.assertEqual(category_title('05_群/00_AI'), '群 · AI')
        monthly = Selection('nonexistent.json', ['05_群'])
        self.assertEqual(monthly.reason('g', '05_群/00_AI/深'), '整类跳过：05_群')
        self.assertIsNone(monthly.reason('g', '06_其它'))
        sc = Scope(own({'skip_categories': ['05_群/00_AI']}), None)
        self.assertEqual(sc.reason('g', '05_群/00_AI/深'), '不在范围内')
        self.assertIsNone(sc.reason('g', '05_群'))
