"""Plugins: optional, found only in the configuration's 插件 folder, and never able to fail a month."""
import json, tempfile, unittest, zipfile
from pathlib import Path
from unittest import mock
from mr import config, jobs, plugins
from mr.__main__ import main


def install(name, code, extra=None):
    d = plugins.folder() / name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'plugin.py').write_text(code, encoding='utf-8')
    for file, text in (extra or {}).items():
        (d / file).write_text(text, encoding='utf-8')


class PluginsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = mock.patch.object(config, 'HOME', Path(tmp.name))
        home.start()
        self.addCleanup(home.stop)
        self.said = []

    def test_none_installed(self):
        self.assertEqual(plugins.installed(), [])
        plugins.after_month('2026-10', {}, self.said.append)
        self.assertEqual(self.said, [])
        self.assertEqual(plugins.command('无此插件', [], {}, self.said.append), 2)

    def test_after_month_and_command_with_the_plugins_own_modules(self):
        install('甲', 'from helper import note\n'
                      'def after_month(month, settings, say): say(note(month))\n'
                      'def command(args, settings, say): say("命令 " + " ".join(args)); return 5\n',
                {'helper.py': 'def note(m): return "写好了 " + m\n'})
        (plugins.folder() / '不是插件').mkdir()          # a folder without plugin.py is not a plugin
        self.assertEqual(plugins.installed(), ['甲'])
        plugins.after_month('2026-10', {}, self.said.append)
        self.assertEqual(plugins.command('甲', ['a', 'b'], {}, self.said.append), 5)
        self.assertEqual(self.said, ['写好了 2026-10', '命令 a b'])

    def test_a_failing_plugin_is_reported_and_the_next_still_runs(self):
        install('坏', 'raise RuntimeError("装坏了")\n')
        install('错', 'def after_month(month, settings, say): raise ValueError("算错了")\n')
        install('好', 'def after_month(month, settings, say): say("好 " + month)\n')
        plugins.after_month('2026-10', {}, self.said.append)
        self.assertEqual(len(self.said), 3)
        self.assertIn('装坏了', self.said[0])
        self.assertIn('好 2026-10', self.said)
        self.assertTrue(any('算错了' in s for s in self.said))

    def test_mr_plugin_lists_and_runs(self):
        install('甲', 'def command(args, settings, say): say("跑了 " + ",".join(args)); return 0\n')
        with mock.patch('builtins.print') as out:
            self.assertEqual(main(['plugin']), 0)
            self.assertIn('甲', out.call_args[0][0])
        with mock.patch('builtins.print') as out:
            self.assertEqual(main(['plugin', '甲', '2026-10', 'all']), 0)
            out.assert_called_with('跑了 2026-10,all')

    def zip_(self, files, name='包.zip'):
        path = config.HOME / name
        config.HOME.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as z:
            for n, t in files.items():
                z.writestr(n, t)
        return path

    def test_install_from_a_package_starts_off_and_runs_once_turned_on(self):
        pkg = self.zip_({'乙/plugin.json': json.dumps({'name': '乙', 'title': '插件乙', 'version': '1.0', 'settings': [{'key': 'where', 'label': '位置'}],
                                                        'actions': [{'id': 'go', 'label': '运行'}]}),
                         '乙/plugin.py': 'def defaults(s): return {"where": "默认"}\n'
                                        'def prepare(s, v): return "准备好了 " + v["where"]\n'
                                        'def after_month(month, settings, say): say("乙 " + month)\n'})
        self.assertEqual(plugins.install(pkg), '乙')
        self.assertFalse(plugins.enabled('乙'))
        plugins.after_month('2026-10', {}, self.said.append)
        self.assertEqual(self.said, [])
        info = plugins.info('乙', {})
        self.assertEqual((info['title'], info['values'], info['actions'][0]['id']), ('插件乙', {'where': '默认'}, 'go'))
        self.assertEqual(plugins.configure('乙', {}, enabled=True), '准备好了 默认')
        plugins.after_month('2026-10', {}, self.said.append)
        self.assertEqual(self.said, ['乙 2026-10'])
        with self.assertRaises(plugins.PluginError):
            plugins.configure('乙', {}, new_values={'别的': 1})

    def test_packages_that_are_refused(self):
        bad = {
            'no manifest': {'丙/plugin.py': ''},
            'no code': {'丙/plugin.json': json.dumps({'name': '丙'})},
            'bad name': {'plugin.json': json.dumps({'name': '../丙'}), 'plugin.py': ''},
            'unsafe path': {'plugin.json': json.dumps({'name': '丙'}), 'plugin.py': '', '../逃出.py': ''},
            'too new': {'plugin.json': json.dumps({'name': '丙', 'requires': '99.0.0'}), 'plugin.py': ''},
        }
        for why, files in bad.items():
            with self.subTest(why), self.assertRaises(plugins.PluginError):
                plugins.install(self.zip_(files))
        self.assertEqual(plugins.installed(), [])
        self.assertFalse((config.HOME.parent / '逃出.py').exists())

    def test_a_plugin_action_is_a_job(self):
        job = {'kind': 'plugin', 'plugin': '甲', 'action': 'backfill'}
        self.assertEqual(jobs.command(job), ['plugin', '甲', 'backfill'])
        self.assertIn('plugin', jobs.LIGHT)
        self.assertEqual(jobs.log_path('D:/月报', job), config.HOME / '插件' / '甲' / 'run.log')


if __name__ == '__main__':
    unittest.main()
