import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from mr import config


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.a, self.b = root / 'a', root / 'b'
        for home in (self.a, self.b):
            home.mkdir()
        (self.a / 'settings.json').write_text(json.dumps({'concurrency': 4}), encoding='utf-8')
        (self.a / 'models.json').write_text(json.dumps({'providers': {'p': {}}, 'models': {}, 'stages': {'read_a': 'm'}}), encoding='utf-8')
        (self.a / '会话选择.json').write_text('{}', encoding='utf-8')
        (self.a / '勾选').mkdir()
        (self.a / '勾选' / '2026-08.json').write_text('{"x": true}', encoding='utf-8')
        (self.a / 'jobs.json').write_text('{}', encoding='utf-8')
        (self.b / 'settings.json').write_text(json.dumps({'concurrency': 9}), encoding='utf-8')
        self.zip = root / 'out' / '消息月报配置.zip'

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_then_import_into_another_home(self):
        with mock.patch.object(config, 'HOME', self.a):
            r = config.export_config(self.zip)
        self.assertEqual(r['files'], ['settings.json', 'models.json', '会话选择.json'])
        self.assertNotIn('jobs.json', zipfile.ZipFile(self.zip).namelist())        # run history stays where it was made
        with mock.patch.object(config, 'HOME', self.b):
            r = config.import_config(self.zip)
            self.assertEqual(config.load_models()['stages'], {'read_a': 'm'})
            self.assertEqual(config.load_settings()['concurrency'], 4)
        self.assertEqual(json.loads((Path(r['backup']) / 'settings.json').read_text(encoding='utf-8')), {'concurrency': 9})
        self.assertEqual((self.b / '勾选' / '2026-08.json').read_text(encoding='utf-8'), '{"x": true}')

    def test_import_from_a_folder(self):
        with mock.patch.object(config, 'HOME', self.b):
            r = config.import_config(self.a)
        self.assertEqual(r['ticks'], 1)
        self.assertTrue((self.b / 'models.json').exists())

    def test_saving_drops_settings_this_version_no_longer_has(self):
        (self.b / 'settings.json').write_text(json.dumps({'concurrency': 9, 'usage_test': ['2026-09']}), encoding='utf-8')
        with mock.patch.object(config, 'HOME', self.b):
            config.save_settings({'notify': False})
        self.assertEqual(json.loads((self.b / 'settings.json').read_text(encoding='utf-8')), {'concurrency': 9, 'notify': False})

    def test_only_text_stages_go_to_an_external_agent(self):
        with mock.patch.object(config, 'HOME', self.b):
            saved = config.save_settings({'external_stages': ['scan_b', 'read_a'], 'external_audit': 10})
            self.assertEqual((saved['external_stages'], saved['external_audit'], saved['external_root']), (['scan_b', 'read_a'], 10, ''))
            for bad in ({'external_stages': ['vision']}, {'external_stages': ['asr']}, {'external_audit': 101}, {'external_audit': True}, {'external_root': 5}):
                with self.assertRaises(config.ConfigError):
                    config.save_settings(bad)
            self.assertEqual(config.load_settings()['external_stages'], ['scan_b', 'read_a'])       # a refused change writes nothing

    def test_a_model_is_priced_per_token_or_local(self):
        m = {'providers': {'p': {}}}
        d = {'provider': 'p', 'model': 'x'}
        self.assertEqual(config._clean_model(m, {**d, 'price': {'input': '2', 'output': 10, 'currency': 'CNY'}})['price'],
                         {'input': 2.0, 'output': 10.0, 'currency': 'CNY'})
        self.assertEqual(config._clean_model(m, {**d, 'price': {'local': True, 'input': 2}})['price'], {'local': True})
        self.assertEqual(config._clean_model(m, {**d, 'price': {}})['price'], {})

    def test_a_key_is_typed_in_or_not_needed(self):
        d = {'base_url': 'https://x/v1', 'api': 'chat'}
        self.assertEqual(config._clean_provider({**d, 'key': {'type': 'credential', 'service': 's'}})['key'], {'type': 'credential'})
        with self.assertRaises(config.ConfigError):
            config._clean_provider({**d, 'key': {'type': 'registry', 'service': 's'}})
        # A provider saved by an earlier version with a key source this one does not have now asks for the key.
        (self.b / 'models.json').write_text(json.dumps({'providers': {'p': {**d, 'key': {'type': 'registry', 'service': 's'}},
                                                                     'q': {**d, 'key': {'type': 'none'}}}}), encoding='utf-8')
        with mock.patch.object(config, 'HOME', self.b), mock.patch.object(config, 'credential_get', return_value=None):
            m = config.load_models()
            self.assertEqual(m['providers']['p']['key'], {'type': 'credential'})
            self.assertEqual(config.resolve_key('q', m['providers']['q']), 'local')
            with self.assertRaises(config.ConfigError):
                config.resolve_key('p', m['providers']['p'])

    def test_the_portable_build_keeps_its_configuration_beside_it(self):
        exe = self.a / 'ChatMonthly.exe'
        with mock.patch.dict('os.environ', {'MR_HOME': '', 'APPDATA': str(self.b)}), \
             mock.patch.object(config.sys, 'frozen', True, create=True), mock.patch.object(config.sys, 'executable', str(exe)):
            self.assertEqual(config._home(), self.b / 'ChatMonthly')             # no 配置 folder: AppData
            (self.a / '配置').mkdir()
            self.assertEqual(config._home(), self.a / '配置')
        with mock.patch.dict('os.environ', {'MR_HOME': '', 'APPDATA': str(self.b)}):
            self.assertEqual(config._home(), self.b / 'ChatMonthly')             # running from source: never the folder beside python

    def test_no_folder_of_this_machine_is_a_default(self):
        self.assertEqual(config.DEFAULTS['archive_root'], '')
        self.assertEqual(Path(config.DEFAULTS['output_root']).parent, Path.home())

    def test_models_keep_the_users_order(self):
        d = {'base_url': 'https://x/v1', 'api': 'chat', 'key': {'type': 'none'}}
        with mock.patch.object(config, 'HOME', self.b), mock.patch.object(config, 'credential_get', return_value=None):
            config.save_provider('p', d)
            for n in ('a', 'b', 'c'):
                config.save_model(n, {'provider': 'p', 'model': n})
            config.set_stage('read_a', 'b')
            self.assertEqual(list(config.move_model('c', -1)['models']), ['a', 'c', 'b'])
            self.assertEqual(list(config.move_model('a', -1)['models']), ['a', 'c', 'b'])    # already first
            m = config.save_model('c2', {'provider': 'p', 'model': 'c'}, old_name='c')         # a rename stays in place
            self.assertEqual(list(m['models']), ['a', 'c2', 'b'])
            m = config.save_model('b2', {'provider': 'p', 'model': 'b'}, old_name='b')
            self.assertEqual((list(m['models']), m['stages']['read_a']), (['a', 'c2', 'b2'], 'b2'))
            config.save_provider('q', d)
            self.assertEqual(list(config.save_provider('p2', d, old_name='p')['providers']), ['p2', 'q'])

    def test_nothing_is_written_from_a_bad_source(self):
        self.zip.parent.mkdir()
        with zipfile.ZipFile(self.zip, 'w') as z:
            z.writestr('settings.json', '{}')
            z.writestr('models.json', 'not json')
        with mock.patch.object(config, 'HOME', self.b):
            with self.assertRaises(config.ConfigError):
                config.import_config(self.zip)
            with self.assertRaises(config.ConfigError):
                config.import_config(self.b / '不存在')
            with self.assertRaises(config.ConfigError):
                config.import_config(self.b)                      # the current folder itself
        self.assertEqual(json.loads((self.b / 'settings.json').read_text(encoding='utf-8')), {'concurrency': 9})
        self.assertFalse((self.b / '导入前备份').exists())


if __name__ == '__main__':
    unittest.main()
