"""Call records left by earlier versions are reduced to what the finished result cost."""
import json, tempfile, unittest
from pathlib import Path
from mr import receipts


def row(stage, key, tokens=10, status='ok', **more):
    return {'stage': stage, 'key': key, 'status': status, 'prompt_tokens': tokens, 'completion_tokens': 0, **more}


class SettleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)

    def write(self, rows, state='done', totals=None, cached=()):
        (self.run / 'receipts.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        (self.run / 'status.json').write_text(json.dumps({'month': '2026-10', 'state': state}), encoding='utf-8')
        if totals is not None:
            (self.run / 'progress.json').write_text(json.dumps({'state': 'done', 'steps': [{'key': k, 'total': n} for k, n in totals.items()]}), encoding='utf-8')
        for stage, key in cached:
            (self.run / 'cache' / stage).mkdir(parents=True, exist_ok=True)
            (self.run / 'cache' / stage / f'{key}-0a1b2c3d.json').write_text('{}', encoding='utf-8')

    def kept(self):
        return [(r['stage'], r['key'], r['prompt_tokens']) for r in map(json.loads, (self.run / 'receipts.jsonl').read_text(encoding='utf-8').splitlines())]

    def test_failed_and_repeated_calls_go(self):
        self.write([row('scan_b', 'a', 1), row('scan_b', 'a', status='error'), row('scan_b', 'a', 2), row('read_a', 'b', 3), row('topic_plan', 'p', 4)])
        self.assertEqual(receipts.settle(self.run), (5, 3))
        self.assertEqual(self.kept(), [('scan_b', 'a', 2), ('read_a', 'b', 3), ('topic_plan', 'p', 4)])
        self.assertTrue(json.loads((self.run / 'status.json').read_text(encoding='utf-8'))['usage_current'])
        self.assertNotIn('tokens_used', json.loads((self.run / 'status.json').read_text(encoding='utf-8')))
        self.assertEqual(len((self.run / 'cache' / 'before-usage-cleanup' / 'receipts.jsonl').read_text(encoding='utf-8').splitlines()), 5)
        self.assertIsNone(receipts.settle(self.run))                    # once

    def test_the_total_in_the_status_follows_the_records(self):
        self.write([row('scan_b', 'a', 100), row('scan_b', 'a', 30), row('read_a', 'b', 5)])
        st = self.run / 'status.json'
        st.write_text(json.dumps({'month': '2026-10', 'state': 'done', 'tokens_used': 135}), encoding='utf-8')
        receipts.settle(self.run)
        self.assertEqual(json.loads(st.read_text(encoding='utf-8'))['tokens_used'], 35)

    def test_calls_whose_answers_left_the_cache_go(self):
        self.write([row('scan_b', 'old', 100), row('scan_b', 'now', 5), row('verify', 'v', 7)], cached=[('scan_b', 'now')])
        receipts.settle(self.run)
        self.assertEqual(self.kept(), [('scan_b', 'now', 5), ('verify', 'v', 7)])     # verify has no cache folder to judge by: kept

    def test_more_blocks_than_the_last_run_read_keeps_the_newest(self):
        rows = [row('scan_b', f's{i}') for i in range(4)] + [row('read_a', 'r0'), row('scan_b', 's4'), row('read_a', 'r1'), row('recall_a', 'c0')]
        self.write(rows, totals={'read': 3, 'recall': 1, 'verify': 0})
        receipts.settle(self.run)
        self.assertEqual([k for _, k, _ in self.kept()], ['r0', 's4', 'r1', 'c0'])    # the three newest of scan and read together

    def test_an_agents_answer_and_the_models_check_of_it_both_stay(self):
        self.write([row('scan_b', 'a', 0, api='external'), row('scan_b', 'a', 9, api='chat')], totals={'read': 1})
        self.assertEqual(receipts.settle(self.run), (2, 2))

    def test_unfinished_results_are_left_alone(self):
        rows = [row('scan_b', 'a'), row('scan_b', 'a', status='error')]
        for state in ('running', 'budget-stop', 'failed', 'external-wait'):
            self.write(rows, state=state)
            self.assertIsNone(receipts.settle(self.run))
            self.assertEqual(len(self.kept()), 2)
        self.write(rows)
        self.assertEqual(receipts.settle(self.run, dry=True), (2, 1))
        self.assertEqual(len(self.kept()), 2)                                         # a dry run writes nothing


if __name__ == '__main__':
    unittest.main()
