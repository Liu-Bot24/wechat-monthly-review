import tempfile, unittest
from pathlib import Path
from mr.selection import Selection, resolve

class FakeArchive:
    folders = {'a@chatroom': '06_低质/x', 'b@chatroom': '05_群/y', 'wxid_c': '01_私聊/z',
               'd@chatroom': '03_工作/分组乙/w', 'e@chatroom': '05_群/v'}
    names = {'a@chatroom': '羊毛群', 'b@chatroom': 'AI一群', 'wxid_c': '张三', 'd@chatroom': '公司大群', 'e@chatroom': 'AI二群'}

class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.s = Selection(Path(self.dir.name) / '会话选择.json', ['06_低质'])

    def tearDown(self):
        self.dir.cleanup()

    def test_default_skips_low_quality_only(self):
        self.assertEqual(self.s.reason('a@chatroom', '06_低质'), '整类跳过：06_低质')
        self.assertIsNone(self.s.reason('b@chatroom', '05_群'))

    def test_include_overrides_category(self):
        self.s.set_chat('a@chatroom', '羊毛群', 'include')
        self.assertIsNone(self.s.reason('a@chatroom', '06_低质'))

    def test_exclude_beats_everything_but_archive_policy(self):
        self.s.set_chat('wxid_c', '张三', 'exclude')
        self.assertEqual(self.s.reason('wxid_c', '01_私聊'), '手动排除')
        self.assertEqual(self.s.reason('wxid_c', '01_私聊', archive_excluded=True), '归档策略排除')
        self.s.set_chat('wxid_c', '张三', 'include')       # switching lists leaves it in one only
        self.assertNotIn('wxid_c', self.s.exclude)
        self.s.set_chat('wxid_c', '张三', 'clear')
        self.assertNotIn('wxid_c', self.s.include)

    def test_second_level_category(self):
        self.s.set_category('03_工作/分组乙', True)
        self.assertEqual(self.s.reason('d@chatroom', '03_工作/分组乙'), '整类跳过：03_工作/分组乙')
        self.assertIsNone(self.s.reason('x@chatroom', '03_工作/业务群'))

    def test_saved_and_reloaded(self):
        self.s.set_chat('wxid_c', '张三', 'exclude')
        self.s.set_category('07_其它', True)
        self.s.save()
        again = Selection(Path(self.dir.name) / '会话选择.json', [])
        self.assertEqual(again.exclude, {'wxid_c': '张三'})
        self.assertEqual(again.skip_categories, ['06_低质', '07_其它'])

    def test_resolve(self):
        found, problems = resolve(FakeArchive, ['wxid_c', '羊毛群', '公司', '张三'])
        self.assertEqual([c for c, _ in found], ['wxid_c', 'a@chatroom', 'd@chatroom'])
        self.assertEqual(problems, [])
        found, problems = resolve(FakeArchive, ['AI', '不存在'])
        self.assertEqual(found, [])
        self.assertIn('匹配到 2 个会话', problems[0])
        self.assertIn('没有匹配到', problems[1])

if __name__ == '__main__':
    unittest.main()
