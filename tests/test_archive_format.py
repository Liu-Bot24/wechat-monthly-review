"""The archive is read through its Markdown alone: any folder, any tool that wrote it, no database."""
import os, tempfile, unittest
from pathlib import Path
from unittest import mock
from mr.archive import Archive, location_problem, month_bounds, parse
from archive_fixture import chat_doc, write_chat

GROUP = [
    ('msg-a1', '2026-10-01 09:00:00', '张三', '文本', 'zs', ' · 提到：me,notify@all', ['zs:', '早 \\*好\\* &lt;b&gt;'], []),
    ('msg-a2', '2026-10-01 09:01', '我', '文件/链接/卡片', 'me', ' · 引用：msg-a1 · 消息：db:2', ['收到', '', '引用 张三：早'], []),
    ('msg-a3', '2026-10-02 10:00:00', '李\\_四', '语音', 'ls', ' · 引用：msg-old', ['\\[语音\\]'], ['../语音/a%201.mp3']),
    ('msg-a4', '2026-10-31 23:59:59', '张三', '图片', 'zs', '', ['\\[图片\\]'], ['<../图片/b 2.jpg>', 'https://example.com/x.jpg']),
]

class ArchiveFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / '任意归档'
        self.index = Path(self.tmp.name) / 'index.sqlite'
        self.opened = []
        write_chat(self.root, '05_群/00_AI/AI群', 'AI群', '2026-10', 'g@chatroom', 'me', GROUP, members=12, title_tail=' · 001')
        write_chat(self.root, '05_群/00_AI/AI群', 'AI群', '2026-09', 'g@chatroom', 'me',
                   [('msg-old', '2026-09-30 22:00:00', '我', '文本', 'me', '', ['旧的'], [])], members=10)
        write_chat(self.root, '01_私聊/张三', '张三', '2026-10', 'zs', 'me',
                   [('msg-p1', '2026-10-05 08:00:00', '张三', '文本', 'zs', '', ['在吗'], [])], sub='记录')
        (self.root / '01_私聊/张三/索引.md').write_text('# 张三\n\n不是聊天记录。\n', encoding='utf-8')
        (self.root / '05_群/00_AI/AI群/语音').mkdir(parents=True)
        (self.root / '05_群/00_AI/AI群/语音/a 1.mp3').write_bytes(b'voice')

    def tearDown(self):
        for a in self.opened:
            a.close()
        self.tmp.cleanup()

    def open(self, **kw):
        a = Archive(self.root, index=self.index, **kw)
        self.opened.append(a)
        return a

    def test_documents_give_chats_folders_and_me(self):
        a = self.open()
        self.assertEqual(a.self_id, 'me')
        self.assertEqual(a.folders, {'g@chatroom': '05_群/00_AI/AI群', 'zs': '01_私聊/张三'})   # the subfolder's name is not fixed
        self.assertEqual(a.names['g@chatroom'], 'AI群')
        self.assertEqual(a.category('g@chatroom'), '05_群/00_AI')
        self.assertEqual(a.categories(), ['01_私聊', '05_群', '05_群/00_AI'])
        self.assertTrue(a.is_group('g@chatroom') and not a.is_group('zs'))
        self.assertEqual(a.person('ls'), '李_四')
        self.assertEqual([r[0] for r in a.find_people('李')], ['ls'])

    def test_messages_and_their_fields(self):
        a = self.open()
        start, end = month_bounds('2026-10')
        m = a.messages('g@chatroom', start, end)
        self.assertEqual([x['anchor'] for x in m], ['msg-a1', 'msg-a2', 'msg-a3', 'msg-a4'])
        self.assertEqual(m[0]['content'], '早 *好* <b>')                       # escaping undone, the "<id>:" line dropped
        self.assertEqual(m[0]['mentions'], ['me', 'notify@all'])
        self.assertEqual(m[1]['quote'], 'msg-a1')
        self.assertEqual(m[1]['content'], '收到\n\n引用 张三：早')
        self.assertEqual(m[1]['timestamp'] - m[0]['timestamp'], 60)          # seconds are optional
        self.assertEqual(m[0]['members'], 12)
        self.assertEqual(m[2]['media'], [{'kind': 'voice', 'path': '05_群/00_AI/AI群/语音/a 1.mp3'}])
        self.assertEqual(m[3]['media'], [{'kind': 'image', 'path': '05_群/00_AI/AI群/图片/b 2.jpg'}])   # web links are not attachments
        self.assertEqual(a.sender_of('g@chatroom', 'msg-old', m[2]['timestamp']), 'me')   # a reply to last month
        self.assertEqual(a.counts('g@chatroom', start, end)[:2], (4, 1))
        self.assertEqual(a.active_chats(start, end), [('g@chatroom', 4), ('zs', 1)])
        self.assertEqual([v['main_path'] for v in a.voices(start, end)], ['05_群/00_AI/AI群/语音/a 1.mp3'])
        shown, k = a.around(m[0]['doc'], 'msg-a1', 2)
        self.assertEqual(([x['anchor'] for x in shown], k), (['msg-old', 'msg-a1', 'msg-a2', 'msg-a3'], 1))

    def test_numbers_stay_and_reports_keep_theirs(self):
        reports = Path(self.tmp.name) / '月报' / '2026-09'
        reports.mkdir(parents=True)
        doc = os.path.relpath(self.root / '05_群/00_AI/AI群/聊天记录/2026-09.md', reports).replace('\\', '/')
        (reports / '月报.md').write_text(f'事情[#20040410](<{doc}#msg-old>)', encoding='utf-8')
        a = self.open(reports=reports.parent)
        self.assertEqual(a.ref(20040410), ('05_群/00_AI/AI群/聊天记录/2026-09.md', 'msg-old'))
        first = a.ids([('d', 'msg-a1'), ('d', 'msg-a2')])
        self.assertGreater(first['msg-a1'], 20040410)
        self.assertEqual(self.open().ids([('d', 'msg-a2'), ('d', 'msg-a1')]), first)

    def test_follows_changes_on_disk(self):
        a = self.open()
        start, end = month_bounds('2026-10')
        moved = self.root / '03_工作/AI群'
        moved.parent.mkdir(parents=True)
        (self.root / '05_群/00_AI/AI群').rename(moved)
        doc = moved / '聊天记录/2026-10.md'
        doc.write_text(doc.read_text(encoding='utf-8') + '\n<a id="msg-a5"></a>\n### 2026-10-31 23:59:59 · 张三 · 文本\n\n发送者：zs\n\n> 新\n',
                       encoding='utf-8')
        a.refresh()
        self.assertEqual(a.category('g@chatroom'), '03_工作')
        self.assertEqual(a.counts('g@chatroom', start, end)[0], 5)

    def test_a_look_with_nothing_changed_keeps_what_is_loaded(self):
        a = self.open()
        with mock.patch.object(a, '_load', wraps=a._load) as load:
            a.refresh(on_disk=a._walk())
            load.assert_not_called()
            # Another opener (a run) bringing the shared index up to date: this one reloads from it.
            write_chat(self.root, '01_私聊/李四', '李四', '2026-10', 'ls', 'me',
                       [('msg-l1', '2026-10-06 08:00:00', '李四', '文本', 'ls', '', ['好'], [])])
            self.open().refresh()
            a.refresh()
            load.assert_called_once()
        self.assertIn('ls', a.folders)

    def test_names_are_kept_as_written(self):
        header, msgs = parse(chat_doc('　', '2026-10', 'b', 'me',
                                      [('msg-b1', '2026-10-01 09:00:00', '小龙 ', '文本', 'b', '', ['在'], [])]))
        self.assertEqual((header['name'], header['month']), ('　', '2026-10'))
        self.assertEqual(msgs[0]['name'], '小龙 ')

    def test_linked_quote_drops_the_whole_quoted_text(self):
        from mr.extract import strip_quote
        self.assertEqual(strip_quote('重审已通过\n引用 南川：账号：x\n插件ID：1\n\n辛苦'), '重审已通过')
        self.assertEqual(strip_quote('收到\n\n引用 张三：早'), '收到')
        self.assertEqual(strip_quote('没有引用：这一行不是引用'), '没有引用：这一行不是引用')

    def test_one_index_builder_at_a_time(self):
        import threading, time
        from mr.archive import one_at_a_time
        lock, got = str(self.index) + '.lock', []
        def take():
            with one_at_a_time(lock):
                got.append(time.time())
        with one_at_a_time(lock):
            t = threading.Thread(target=take)
            started = time.time()
            t.start()
            time.sleep(0.8)
            self.assertEqual(got, [])                                       # waits while another holds it
        t.join(5)
        self.assertEqual(len(got), 1)
        self.assertGreater(got[0] - started, 0.7)

    def test_two_openers_at_once(self):
        import threading
        out, errors = [], []
        def open_one():
            try:
                out.append(self.open())
            except Exception as e:
                errors.append(e)
        ts = [threading.Thread(target=open_one) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual([len(a.folders) for a in out], [2, 2])

    def test_not_a_chat_document(self):
        self.assertEqual(parse('# 普通文档\n\n<a id="x"></a>\n### 标题\n'), (None, []))
        header, msgs = parse(chat_doc('甲', '2026-10', 'a', 'me', []))
        self.assertEqual(msgs, [])

    def test_unset_or_missing_locations(self):
        self.assertIsNotNone(location_problem(''))
        self.assertIsNotNone(location_problem(self.root / '没有'))
        self.assertIsNone(location_problem(self.root))
        with self.assertRaises(SystemExit):
            Archive('', index=self.index)

if __name__ == '__main__':
    unittest.main()
