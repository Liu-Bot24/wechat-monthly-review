import json, tempfile, threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from mr import pipeline
from mr.pipeline import Pipeline


class FakeModels:
    def __init__(self):
        self.lock, self.failures_in_a_row, self.waiting = threading.Lock(), 6, {}


class RetryTest(unittest.TestCase):
    def run_jobs(self, models):
        p = Pipeline.__new__(Pipeline)
        p.settings, p.progress = {'concurrency': 2}, None
        tries = {'flaky': 0, 'slow': 0, 'broken': 0, 'fine': 0}
        def job(name, fails):
            def fn():
                tries[name] += 1
                if tries[name] <= fails:
                    raise RuntimeError(f'{name} 调用失败')
                return name
            return fn
        jobs = [('flaky', job('flaky', 1)), ('slow', job('slow', 2)), ('broken', job('broken', 99)), ('fine', job('fine', 0))]
        with mock.patch.object(pipeline, 'RETRY_PAUSES', (0, 0)), mock.patch('builtins.print'):
            results, failures = p._parallel(jobs, None, models)
        return results, failures, tries

    def test_failed_jobs_get_a_second_round(self):
        models = FakeModels()
        results, failures, tries = self.run_jobs(models)
        self.assertEqual(results, {'flaky': 'flaky', 'slow': 'slow', 'fine': 'fine'})
        self.assertEqual(list(failures), ['broken'])
        self.assertEqual(tries, {'flaky': 2, 'slow': 3, 'broken': 3, 'fine': 1})   # what succeeded is not asked again
        self.assertEqual(models.failures_in_a_row, 0)                  # the pause clears the run of failures

    def test_a_stage_that_ends_with_failures_does_not_stop_the_next(self):
        models = FakeModels()
        p = Pipeline.__new__(Pipeline)
        p.settings, p.progress = {'concurrency': 2}, None
        def broken():
            with models.lock:
                models.failures_in_a_row += 1
            raise RuntimeError('看图 调用失败')
        with mock.patch.object(pipeline, 'RETRY_PAUSES', (0, 0)), mock.patch('builtins.print'):
            _, failures = p._parallel([(str(i), broken) for i in range(8)], None, models)
        self.assertEqual(len(failures), 8)
        self.assertEqual(models.failures_in_a_row, 0)

    def test_no_second_round_without_models(self):
        results, failures, tries = self.run_jobs(None)
        self.assertEqual(set(failures), {'flaky', 'slow', 'broken'})
        self.assertEqual(tries, {'flaky': 1, 'slow': 1, 'broken': 1, 'fine': 1})


class ExternalWaitTest(unittest.TestCase):
    """Jobs waiting for an external agent are neither results nor failures; the run stops once the others are done."""
    def run_jobs(self, hold):
        from mr.llm import ExternalPending
        models = FakeModels()
        p = Pipeline.__new__(Pipeline)
        p.settings, p.progress = {'concurrency': 2}, None
        done = []
        def waits():
            models.waiting[('scan_b', 'k')] = 'task'
            raise ExternalPending('scan_b')
        with mock.patch.object(pipeline, 'RETRY_PAUSES', (0, 0)), mock.patch('builtins.print'):
            try:
                return p._parallel([('a', waits), ('b', lambda: done.append('b') or 'b')], None, models, hold=hold), done
            except ExternalPending:
                return 'stopped', done

    def test_the_stage_finishes_then_stops(self):
        self.assertEqual(self.run_jobs(hold=False), ('stopped', ['b']))

    def test_held_until_the_caller_is_ready(self):
        self.assertEqual(self.run_jobs(hold=True), (({'b': 'b'}, {}), ['b']))


class AnswerChecksTest(unittest.TestCase):
    """Checks on a reading answer, the same for a model's and an external agent's."""
    def pipe(self):
        p = Pipeline.__new__(Pipeline)
        p.index = {f'm{i}': {'in_month': i != 9} for i in range(1, 40)}
        return p

    def test_numbers_must_mostly_be_from_the_block(self):
        chunk = {'sids': ['m1', 'm2', 'm3', 'm9']}
        check = self.pipe()._validate_cards(chunk)
        answer = {'cards': [{'msg_ids': ['m1', 'm9', 'm77']}]}
        check(answer)                                                           # one stray number is dropped, not refused
        self.assertEqual(answer['cards'][0]['msg_ids'], ['m1'])                 # m9 is context from outside the month
        with self.assertRaises(ValueError):
            check({'cards': [{'msg_ids': ['m1', 'm70', 'm71', 'm72']}]})        # written from something else
        with self.assertRaises(KeyError):
            check({'topics': []})

    def test_scan_answer_is_put_in_shape(self):
        check = self.pipe()._validate_scan({'sids': ['m1', 'm2']})
        answer = {'note': 'n', 'topics': [{'msg_ids': ['m1']}, 'x'], 'links': None, 'images': {'a': 1}}
        check(answer)
        self.assertEqual(answer, {'note': 'n', 'topics': [{'msg_ids': ['m1']}], 'links': [], 'images': [], 'escalations': []})
        with self.assertRaises(ValueError):
            check({'topics': [{'msg_ids': ['m50', 'm51', 'm52', 'm1']}]})

    def test_coverage_counts_what_the_other_reader_found(self):
        sids = [f'm{i}' for i in range(1, 40)]
        theirs = {'cards': [{'msg_ids': ['m3'], 'importance': 4}, {'msg_ids': ['m20']}, {'msg_ids': ['m30'], 'importance': 2}]}
        self.assertEqual(pipeline.coverage(sids, {'cards': [{'msg_ids': ['m3']}, {'msg_ids': ['m24']}]}, theirs), (2, 2))   # m24 is beside m20
        self.assertEqual(pipeline.coverage(sids, {'cards': [{'msg_ids': ['m3']}, {'msg_ids': ['m26']}]}, theirs), (2, 1))   # m26 is not
        self.assertEqual(pipeline.coverage(sids, {'cards': []}, theirs), (2, 0))
        scan = {'topics': [{'msg_ids': ['m5'], 'value': 4}, {'msg_ids': ['m12'], 'value': 3}], 'escalations': [{'msg_ids': ['m33']}]}
        self.assertEqual(pipeline.coverage(sids, {'topics': [{'msg_ids': ['m6'], 'value': 3}], 'links': [{'msg_id': 'm35'}]}, scan), (2, 2))
        self.assertEqual(pipeline.coverage(sids, {'topics': [], 'links': 'x'}, {'topics': []}), (0, 0))


class ChecksTest(unittest.TestCase):
    """A finished result never ends with a list of checks: each is fixed while the result is written."""

    def test_refs(self):
        p = SimpleNamespace(index={'m1': {'in_month': True, 'lookup_id': 7, 'doc': 'a', 'anchor': 'msg-1', 'ts': 0},
                                   'm2': {'in_month': False, 'lookup_id': 8, 'doc': 'b', 'anchor': 'msg-2', 'ts': 0}},
                            archive=SimpleNamespace(link=lambda doc, anchor, run_dir: f'<{doc}#{anchor}>'), run_dir=None)
        out = Pipeline.link_refs(p, '说过 [m1]，上月 [m2]，编造的 [m9]。')
        self.assertEqual(out, '说过 [#7](<a#msg-1>)，上月 [#8](<b#msg-2>)，编造的 。')

    def _old_run(self, d, failures=None):
        report = d / '月报_2019-05.md'
        report.write_text('# 回顾\n\n- 群里说 密码：Qwe#98765 [?]\n- 见 [#3](<../a.md#msg-1>)\n\n### 校验提示\n\n- 报告中检测到疑似未遮蔽的敏感值：密码\n',
                          encoding='utf-8')
        (d / 'status.json').write_text(json.dumps({'state': 'done-with-problems', 'report': str(report), 'failures': failures or {},
                                                   'problems': ['报告中检测到疑似未遮蔽的敏感值：密码']}, ensure_ascii=False), encoding='utf-8')
        (d / 'progress.json').write_text(json.dumps({'state': 'done-with-problems', 'message': '月报已生成：用掉 9 token；校验提示 1 条'},
                                                    ensure_ascii=False), encoding='utf-8')
        return report

    def test_settle_fixes_an_older_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            report = self._old_run(d)
            self.assertTrue(pipeline.settle(d))
            text = report.read_text(encoding='utf-8')
            self.assertNotIn('校验提示', text)
            self.assertNotIn('Qwe#98765', text)
            self.assertNotIn('[?]', text)
            self.assertIn('⟦密码·P1⟧', text)
            self.assertIn('[#3](<../a.md#msg-1>)', text)
            st = json.loads((d / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual((st['state'], st['problems']), ('done', []))
            prog = json.loads((d / 'progress.json').read_text(encoding='utf-8'))
            self.assertEqual((prog['state'], prog['message']), ('done', '月报已生成：用掉 9 token'))
            self.assertIn('Qwe#98765', (d / 'cache' / 'before-settle' / report.name).read_text(encoding='utf-8'))
            self.assertFalse(pipeline.settle(d))          # nothing left to fix

    def test_settle_keeps_failed_calls_to_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._old_run(d, failures={'read_a k1': '503'})
            self.assertTrue(pipeline.settle(d))
            st = json.loads((d / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual((st['state'], st['problems']), ('done-with-problems', []))

    def test_settle_leaves_running_and_clean_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'status.json').write_text(json.dumps({'state': 'done', 'problems': []}), encoding='utf-8')
            self.assertFalse(pipeline.settle(d))
            (d / 'status.json').write_text(json.dumps({'state': 'running', 'problems': ['x']}), encoding='utf-8')
            self.assertFalse(pipeline.settle(d))


class HistoryTest(unittest.TestCase):
    def test_older_records_lose_their_checks(self):
        from mr.jobs import Jobs
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jobs.json'
            path.write_text(json.dumps({'history': [
                {'month': '2019-05', 'state': 'done-with-problems', 'message': '月报已生成：用掉 9 token；校验提示 3 条'},
                {'month': '2019-04', 'state': 'done-with-problems', 'message': '月报已生成：用掉 9 token；2 块失败；校验提示 1 条'},
                {'month': 'asr', 'state': 'done-with-problems', 'message': '转写完成：3 条失败'}]}, ensure_ascii=False), encoding='utf-8')
            h = Jobs._load(SimpleNamespace(path=path))['history']
            self.assertEqual([(r['state'], r['message']) for r in h], [
                ('done', '月报已生成：用掉 9 token'), ('done-with-problems', '月报已生成：用掉 9 token；2 块失败'),
                ('done-with-problems', '转写完成：3 条失败')])


if __name__ == '__main__':
    unittest.main()


class ModelIdsTest(unittest.TestCase):
    def test_ids_takes_one_number_a_list_or_nested_lists(self):
        self.assertEqual(pipeline.ids('m1'), ['m1'])
        self.assertEqual(pipeline.ids(['m1', ['m2', 3], None]), ['m1', 'm2'])
        self.assertEqual(pipeline.ids(None), [])

    def test_material_reads_a_link_whose_number_is_a_list(self):
        p = Pipeline.__new__(Pipeline)
        p.index = {'m1': {}, 'm2': {}}
        p.chunks = [{'key': 'k', 'stage': 'scan_b', 'chat': 'c'}]
        p.esc_chunks = p.recall_chunks = []
        answer = {'topics': [{'title': 't', 'msg_ids': [['m1'], 'm9'], 'value': 3}],
                  'links': [{'msg_id': 'm1', 'title': 'a'}, {'msg_id': ['m1', 'm2'], 'title': 'b'}, {'msg_id': ['m9']}]}
        p.material({'k': answer})
        got = [(t['title'], t['msg_ids']) for t in p.topics['c']]
        self.assertEqual(got, [('t', ['m1']), ('链接：a', ['m1']), ('链接：b', ['m1', 'm2'])])
