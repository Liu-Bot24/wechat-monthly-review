"""What changed in the archive since a report was written, and keeping the report as it is."""
import os, tempfile, time, unittest
from pathlib import Path
from archive_fixture import write_chat
from mr import source
from mr.archive import Archive
from mr.selection import Selection

MONTH = '2026-10'


class SourceChangeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / '归档'
        self.docs = {}
        self.write('01_私聊/张三', '张三', 'zhang', 5)
        self.write('05_群/大群', '大群', 'big@chatroom', 120, members=300)
        self.write('05_群/闲聊群', '闲聊群', 'idle@chatroom', 40, members=300)
        self.selection = Selection(Path(self.tmp.name) / '会话选择.json', [])
        self.selection.set_chat('idle@chatroom', '闲聊群', 'exclude')

    def write(self, folder, name, chat, n, **kw):
        msgs = [(f'msg-{chat[:3]}{i:021x}', f'2026-10-{1 + i % 28:02d} 09:{i % 60:02d}:{i // 60 % 60:02d}', '对方', '文本', 'x', '', [f'第 {i} 句'], []) for i in range(n)]
        self.docs[chat] = write_chat(self.root, folder, name, MONTH, chat, 'me', msgs, **kw)

    def archive(self):
        a = Archive(self.root, index=Path(self.tmp.name) / 'index.sqlite')
        self.addCleanup(a.close)
        return a

    def test_exact_change_per_chat_when_the_run_recorded_it(self):
        st = {'source_messages': 165, 'source_chats': {'zhang': 5, 'big@chatroom': 100, 'idle@chatroom': 40, 'gone@chatroom': 20}}
        d = source.detail(self.archive(), self.selection, MONTH, st)
        self.assertEqual((d['then'], d['now'], d['rewritten']), (165, 165, None))
        self.assertEqual([(c['name'], c['then'], c['now'], c['read']) for c in d['chats']],
                         [('gone@chatroom', 20, 0, False), ('大群', 100, 120, True)])       # unchanged chats are not listed

    def test_an_older_report_lists_the_chats_written_again(self):
        made = time.time() - 3600
        for chat in ('zhang', 'idle@chatroom'):                 # these were not touched after the report was made
            os.utime(self.docs[chat], (made - 60, made - 60))
        st = {'source_messages': 100, 'updated': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(made))}
        report = ('## 附录\n\n### 覆盖\n\n| 分类 | 会话 | 消息 | 我发 | 精读 | 精读+扫读 | 扫读 |\n| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n'
                  '| 01_私聊 | 1 | 5 | 0 | 1 | 0 | 0 |\n| 05_群 | 2 | 1,095 | 3 | 0 | 0 | 2 |\n| 07_其它 | 1 | 9 | 0 | 0 | 0 | 1 |\n\n'
                  '跳过：手动排除 1 个会话 / 40 条，没有读取，也没有发给模型。\n\n### 补充处理\n\n- 语音：没有转写。\n')
        d = source.detail(self.archive(), self.selection, MONTH, st, None, report)
        self.assertEqual((d['chats'], d['now']), (None, 165))
        self.assertEqual([(c['name'], c['now'], c['read']) for c in d['rewritten']], [('大群', 120, True)])
        self.assertEqual(d['categories'], [{'category': '05_群', 'then_chats': 2, 'then': 1095, 'now_chats': 1, 'now': 120},
                                           {'category': '07_其它', 'then_chats': 1, 'then': 9, 'now_chats': 0, 'now': 0}])   # 私聊 is as it was
        self.assertEqual((d['reading'], d['skipped']), ({'then': 1109, 'now': 125}, {'then': 40, 'now': 40}))
        self.assertIsNone(source.detail(self.archive(), self.selection, MONTH, st)['categories'])       # a report without the table
        self.assertEqual(source.read_at({'source_read_at': 5, 'updated': 'x'}), 5)
        self.assertEqual(source.read_at({}, {'started': 7.5, 'state': 'done'}), 7.5)
        self.assertIsNone(source.read_at({}, {'started': 7.5, 'state': 'failed'}))

    def test_keeping_the_report_clears_the_notice_until_the_next_change(self):
        a = self.archive()
        st = {'source_messages': 100}
        self.assertTrue(source.big_enough(st['source_messages'], a.month_messages(*source.month_bounds(MONTH))))
        source.accept(a, MONTH, st)
        self.assertEqual((st['source_messages'], st['source_chats']), (165, {'zhang': 5, 'big@chatroom': 120, 'idle@chatroom': 40}))
        self.assertEqual((st['source_accepted']['then'], st['source_accepted']['now']), (100, 165))
        self.assertFalse(source.big_enough(st['source_messages'], 165))
        self.assertEqual(source.detail(a, self.selection, MONTH, st)['chats'], [])
        self.assertFalse(source.big_enough(10000, 10049))        # under 50 messages and under half a percent
        self.assertTrue(source.big_enough(10000, 10050))


if __name__ == '__main__':
    unittest.main()
