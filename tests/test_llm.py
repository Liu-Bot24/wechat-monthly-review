"""Model calls: fallbacks when a stage's model keeps failing, and images over either API."""
import json, tempfile, unittest
from pathlib import Path
from unittest import mock
from mr import config, llm
from mr.llm import Models


def models_config(fallback=('plain', 'seeing'), enabled=True):
    p = {'base_url': 'http://127.0.0.1:9', 'api': 'chat', 'key': {'type': 'none'}}
    spec = lambda model, vision: {'provider': 'p', 'model': model, 'params': {}, 'vision': vision, 'price': {}, 'note': ''}
    return {'providers': {'p': p, 'asr': {**p, 'api': 'audio'}},
            'models': {'main': spec('m-main', True), 'plain': spec('m-plain', False), 'seeing': spec('m-seeing', True),
                       'whisper': {**spec('w', False), 'provider': 'asr'}},
            'stages': {'vision': 'main', 'at_me': 'main'},
            'fallback': {'enabled': enabled, 'models': list(fallback)}}


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        sleep = mock.patch.object(llm.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def call(self, m, stage, answers, images=None):
        """answers: model name -> True (answers) or False (always fails). Returns (answer or exception, models asked)."""
        asked = []
        def chat(client, spec, system, user, json_mode, images=None):
            asked.append(spec['model'])
            if not answers.get(spec['model']):
                raise ValueError('坏了')
            return json.dumps({'ok': spec['model']}), Models._usage(10, 2), 'stop'
        models = Models(m, Path(self.tmp.name))
        with mock.patch.object(Models, '_chat', side_effect=chat):
            try:
                return models.call(stage, 'k1', 'sys', 'user', images=images), asked, models
            except RuntimeError as e:
                return e, asked, models

    def test_falls_back_in_order_and_keeps_the_answer(self):
        answer, asked, models = self.call(models_config(), 'at_me', {'m-seeing': True})
        self.assertEqual(answer, {'ok': 'm-seeing'})
        self.assertEqual(asked, ['m-main'] * 3 + ['m-plain'] * 3 + ['m-seeing'])
        self.assertEqual(models.cached('at_me', 'k1', 'sys'), {'ok': 'm-seeing'})     # resuming does not ask again
        receipts = [json.loads(l) for l in models.receipts.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(receipts[-1]['model_id'], 'seeing')
        self.assertEqual(receipts[-1]['fallback'], 2)
        self.assertEqual(models.failures_in_a_row, 0)

    def test_an_image_stage_only_uses_fallbacks_that_take_images(self):
        answer, asked, _ = self.call(models_config(), 'vision', {'m-plain': True, 'm-seeing': True}, images=['data:image/png;base64,AA'])
        self.assertEqual(answer, {'ok': 'm-seeing'})
        self.assertNotIn('m-plain', asked)

    def test_turned_off_or_all_failing(self):
        answer, asked, models = self.call(models_config(enabled=False), 'at_me', {'m-plain': True})
        self.assertIsInstance(answer, RuntimeError)
        self.assertEqual(asked, ['m-main'] * 3)
        self.assertEqual(models.failures_in_a_row, 1)
        answer, asked, _ = self.call(models_config(), 'at_me', {})
        self.assertIsInstance(answer, RuntimeError)
        self.assertEqual(len(asked), 9)

    def test_fallbacks_for(self):
        m = models_config(('whisper', 'main'))
        self.assertEqual(config.fallbacks_for(m, 'at_me'), ['main'])        # a transcription model never stands in
        self.assertEqual(config.fallbacks_for(m, 'asr'), [])


class ChatImagesTest(unittest.TestCase):
    def test_images_go_as_image_url_parts(self):
        sent = {}
        class Completions:
            def create(self, **kw):
                sent.update(kw)
                msg = mock.Mock(content='{"results": []}')
                return mock.Mock(usage=None, choices=[mock.Mock(message=msg, finish_reason='stop')])
        client = mock.Mock()
        client.chat.completions = Completions()
        text, _, finish = Models._chat(client, {'model': 'vl', 'params': {}}, 'sys', '看图', True, ['data:image/png;base64,AA'])
        self.assertEqual(finish, 'stop')
        self.assertEqual(sent['messages'][1]['content'],
                         [{'type': 'text', 'text': '看图'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA'}}])
        Models._chat(client, {'model': 'vl', 'params': {}}, 'sys', '只有字', True)
        self.assertEqual(sent['messages'][1]['content'], '只有字')


class ExternalTest(unittest.TestCase):
    """A stage handed to an external agent: a task file out, a result file in, read the way an API answer is."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        sleep = mock.patch.object(llm.time, 'sleep')
        sleep.start()
        self.addCleanup(sleep.stop)
        self.fresh()

    def fresh(self, name='run'):
        """A run folder that has asked nothing yet (a real run makes its folder before the first call)."""
        self.run_dir, self.out = Path(self.tmp.name) / name, Path(self.tmp.name) / name / 'handoff'
        self.run_dir.mkdir()

    def models(self, stages=('at_me',), audit=0):
        return Models(models_config(), self.run_dir,
                      external={'stages': set(stages), 'root': self.out, 'owner': Path('2026-10'), 'dirs': {}, 'audit': audit})

    def ask(self, models, validate=None, compare=None, json_mode=True, api=None):
        """One call. api: what the stage's model would answer when asked; None fails the test if it is asked."""
        def chat(client, spec, system, user, json_mode, images=None):
            self.assertIsNotNone(api, '模型不该被调用')
            return json.dumps(api), Models._usage(10, 2), 'stop'
        with mock.patch.object(Models, '_chat', side_effect=chat):
            return models.call('at_me', 'k1', 'sys', '原文', json_mode=json_mode, validate=validate, compare=compare)

    def files(self, sub):
        d = self.out / 'at_me' / '2026-10' / sub
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def result_path(self, models, suffix='.json'):
        (task,) = [t for (_, _), t in models.waiting.items()]
        return task.parent.parent / '结果' / (task.stem + suffix)

    def test_a_task_goes_out_and_its_result_comes_back(self):
        m = self.models()
        with self.assertRaises(llm.ExternalPending):
            self.ask(m)
        (task,) = (self.out / 'at_me' / '2026-10' / '任务').iterdir()
        text = task.read_text(encoding='utf-8')
        result = self.result_path(m)
        self.assertTrue(text.startswith(f'结果文件：{result}\n'))
        self.assertIn('【要求】\n\nsys\n\n【用户消息】\n\n原文\n', text)
        result.write_text('```json\n{"items": [1]}\n```\n', encoding='utf-8')          # a fenced object is the object
        m = self.models()
        self.assertEqual(self.ask(m), {'items': [1], '_external': True})
        self.assertEqual(m.cached('at_me', 'k1', 'sys'), {'items': [1], '_external': True})
        self.assertEqual((self.files('任务'), self.files('结果')), ([], []))
        receipt = json.loads(m.receipts.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((receipt['api'], receipt['status'], receipt['prompt_tokens']), ('external', 'ok', 0))
        again = self.models()
        self.assertEqual(self.ask(again)['items'], [1])                               # from the cache; no file needed
        self.assertEqual(again.external_used, {'at_me': 1})

    def test_a_result_that_fails_the_checks_is_set_aside_with_the_reason(self):
        def validate(answer):
            if 'items' not in answer:
                raise ValueError('缺 items')
        for bad in ('不是 JSON', '[1, 2]', '{"other": 1}', '{"items": [], "_refused": true}', ''):
            m = self.models()
            with self.assertRaises(llm.ExternalPending):
                self.ask(m, validate)
            self.result_path(m).write_text(bad, encoding='utf-8')
            m = self.models()
            with self.assertRaises(llm.ExternalPending):
                self.ask(m, validate)
            self.assertEqual(m.rejected, {'at_me': 1}, bad)
            self.assertEqual(len(self.files('任务')), 1)                              # still to do
            self.assertEqual(self.files('结果'), [])
            self.assertEqual({Path(n).suffix for n in self.files('退回')}, {'.json', '.txt'})
            self.assertIsNone(m.cached('at_me', 'k1', 'sys'))
        self.result_path(m).write_text('{"items": []}', encoding='utf-8')
        self.assertEqual(self.ask(self.models(), validate), {'items': [], '_external': True})
        self.assertEqual(self.files('退回'), [])

    def test_markdown_stages_and_other_stages(self):
        m = self.models()
        with self.assertRaises(llm.ExternalPending):
            self.ask(m, json_mode=False)
        self.result_path(m, '.md').write_text('# 标题\n', encoding='utf-8')
        self.assertEqual(self.ask(self.models(), json_mode=False), {'markdown': '# 标题', '_external': True})
        self.fresh('other')
        other = self.models(stages=())                                               # not handed out: the model answers
        self.assertEqual(self.ask(other, json_mode=False, api={'x': 1})['markdown'], '{"x": 1}')

    def test_a_sampled_answer_is_compared_with_the_models(self):
        compare = lambda mine, theirs: (len(theirs['items']), len(set(theirs['items']) & set(mine['items'])))
        def run(agent, model, audit=100):
            self.fresh(f'run-{agent}-{audit}')
            m = self.models(audit=audit)
            with self.assertRaises(llm.ExternalPending):
                self.ask(m, compare=compare)
            self.result_path(m).write_text(json.dumps({'items': agent}), encoding='utf-8')
            m = self.models(audit=audit)
            return self.ask(m, compare=compare, api=model and {'items': model}), m
        answer, m = run([1, 2, 3], [1, 2])                       # covers what the model found: the agent's answer stays
        self.assertEqual(answer, {'items': [1, 2, 3], '_external': True, '_audit': {'items': 2, 'covered': 2}})
        self.assertEqual(m.audit, {'checked': 1, 'items': 2, 'covered': 2, 'replaced': 0})
        answer, m = run([1], [1, 2])                             # misses one: the model's answer is used
        self.assertEqual(answer, {'items': [1, 2], '_audit': {'items': 2, 'covered': 1}})
        self.assertEqual((m.audit['replaced'], m.external_used), (1, {}))
        resumed = self.models(audit=100)
        self.ask(resumed, compare=compare)                       # the outcome is read back from the kept answer
        self.assertEqual(resumed.audit, {'checked': 1, 'items': 2, 'covered': 1, 'replaced': 1})
        answer, m = run([1], None, audit=0)                      # no sampling: the model is not asked
        self.assertEqual(answer, {'items': [1], '_external': True})

    def test_an_answer_stands_when_the_comparison_cannot_be_made(self):
        m = self.models(audit=100)
        with self.assertRaises(llm.ExternalPending):
            self.ask(m)
        self.result_path(m).write_text('{"items": [1]}', encoding='utf-8')
        m = self.models(audit=100)
        with mock.patch.object(Models, '_client', side_effect=config.ConfigError('还没有保存密钥')):     # the stage's model cannot be reached at all
            answer = m.call('at_me', 'k1', 'sys', '原文', compare=lambda mine, theirs: (1, 1))
        self.assertEqual(answer, {'items': [1], '_external': True})
        self.assertEqual(m.cached('at_me', 'k1', 'sys'), answer)

    def test_the_agent_can_run_the_same_checks_before_it_reports(self):
        from mr import checks, external
        from mr.__main__ import main
        validate = checks.make('at_me', wanted=['m1', 'm2'])
        m = self.models()
        with self.assertRaises(llm.ExternalPending):
            self.ask(m, validate)
        result = self.result_path(m)
        self.assertEqual([r[2] for r in external.check_folder(self.out)], ['missing'])
        result.write_text('{"items": [{"msg_id": "m1"}]}', encoding='utf-8')
        (row,) = external.check_folder(self.out)
        self.assertEqual(row[2:], ('bad', "漏了 ['m2']"))
        with mock.patch('builtins.print') as shown:
            self.assertEqual(main(['check', str(self.out)]), 1)
        self.assertIn("漏了 ['m2']", shown.call_args[0][0])
        self.assertIn('还没有全部合格', (self.out / '检查结果.txt').read_text(encoding='utf-8'))
        self.assertTrue(result.exists())                                              # checking moves nothing
        # The run refuses it for the same reason the check gave.
        m = self.models()
        with self.assertRaises(llm.ExternalPending):
            self.ask(m, validate)
        self.assertEqual((self.out / 'at_me' / '2026-10' / '退回' / (result.stem + '.txt')).read_text(encoding='utf-8').strip(), "漏了 ['m2']")
        result.write_text('{"items": [{"msg_id": ["m1", "m2"]}]}', encoding='utf-8')
        with mock.patch('builtins.print'):
            self.assertEqual(main(['check', str(self.out)]), 0)
        m = self.models()
        self.assertEqual(self.ask(m, validate)['items'], [{'msg_id': ['m1', 'm2']}])   # and takes what the check passed
        state = m.external_state()
        self.assertFalse(self.out.exists())                                           # nothing left behind, the report included
        self.assertIn(' check "', state['prompt'])

    def test_state_lists_what_waits_and_clears_the_rest(self):
        m = self.models()
        with self.assertRaises(llm.ExternalPending):
            self.ask(m)
        stale = self.out / 'at_me' / '2026-10' / '任务' / 'old-00000000.md'
        stale.write_text('旧任务', encoding='utf-8')
        keep = self.out / '说明.txt'                              # not this module's file: left alone
        keep.write_text('x', encoding='utf-8')
        state = m.external_state()
        self.assertEqual((state['pending'], state['rejected'], state['folder']), ({'at_me': 1}, {}, str(self.out)))
        self.assertIn(str(self.out), state['prompt'])
        self.assertFalse(stale.exists())
        self.assertTrue(keep.exists())
        self.assertEqual(len(self.files('任务')), 1)
        self.assertTrue(self.result_path(m).parent.is_dir())     # where the agent is told to write still exists


class CurrentUsageTest(unittest.TestCase):
    """When a result is finished, the receipts hold what that result cost and nothing of earlier versions."""
    def test_only_the_calls_behind_the_answers_in_use_stay(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            row = lambda stage, key, tokens, **more: json.dumps({'stage': stage, 'key': key, 'status': 'ok', 'model': 'm', 'prompt_tokens': tokens,
                                                                 'completion_tokens': 0, 'seconds': 1, 'at': '2026-11-01T00:00:00', **more})
            m = Models(models_config(), run)
            name = lambda key: m._path('at_me', key, 'sys').stem
            (run / 'receipts.jsonl').write_text('\n'.join([
                row('at_me', 'old', 100),                                    # an earlier version's block: not asked this time
                row('at_me', 'k1', 7),                                       # the same block asked under an older prompt, before answers were named
                row('at_me', 'k1', 20),
                row('at_me', 'k2', 30, answer=name('k2')),
                row('at_me', 'k2', 1, answer='k2-00000000'),                 # another prompt's answer for this block
                row('at_me', 'k3', 0, answer=name('k3'), api='external'),    # an agent's answer and the model's, compared with it
                row('at_me', 'k3', 40, answer=name('k3'), api='chat'),
                json.dumps({'stage': 'at_me', 'key': 'k2', 'status': 'error', 'at': '2026-11-01T00:00:00'}),
                row('topic_plan', 'p', 5),                                   # asked outside any run
            ]) + '\n', encoding='utf-8')
            for key in ('k1', 'k2', 'k3'):
                m._keep(m._path('at_me', key, 'sys'), {'items': []})
                m.call('at_me', key, 'sys', 'user')                          # from the cache: no new call, but in use
            m.keep_current()
            kept = [json.loads(l) for l in (run / 'receipts.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([(r['stage'], r['key'], r['prompt_tokens']) for r in kept],
                             [('at_me', 'k1', 20), ('at_me', 'k2', 30), ('at_me', 'k3', 0), ('at_me', 'k3', 40), ('topic_plan', 'p', 5)])
            self.assertEqual(m.used, 95)


class FallbackSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = mock.patch.object(config, 'HOME', Path(self.tmp.name))
        home.start()
        self.addCleanup(home.stop)
        m = models_config(fallback=())
        del m['fallback']
        config._save_models(m)

    def test_defaults_choose_rename_and_delete(self):
        self.assertEqual(config.load_models()['fallback'], {'enabled': True, 'models': ['', '']})
        config.set_fallback(models=['seeing', ''])
        with self.assertRaises(config.ConfigError):
            config.set_fallback(models=['seeing', 'seeing'])
        with self.assertRaises(config.ConfigError):
            config.set_fallback(models=['whisper'])
        config.save_model('看图备用', config.load_models()['models']['seeing'], old_name='seeing')
        self.assertEqual(config.load_models()['fallback']['models'], ['看图备用', ''])
        with self.assertRaises(config.ConfigError):
            config.delete_model('看图备用')                                   # still a fallback
        config.set_fallback(enabled=False)
        self.assertEqual(config.load_models()['fallback'], {'enabled': False, 'models': ['看图备用', '']})


if __name__ == '__main__':
    unittest.main()
