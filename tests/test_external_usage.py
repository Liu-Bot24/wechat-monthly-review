"""A month an external agent helped with is told apart from one made wholly through the interfaces: its agent's
answers are counted by block, and its total never feeds the estimate of a run through the interfaces."""
import json, tempfile, unittest
from pathlib import Path
from mr import pipeline, receipts


def row(stage, key, api='responses', status='ok', tokens=0):
    return {'stage': stage, 'key': key, 'api': api, 'status': status, 'prompt_tokens': tokens, 'completion_tokens': 0}


class ExternalUsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def month(self, name, est, used, rows, seconds=100):
        d = self.root / name
        d.mkdir()
        (d / 'status.json').write_text(json.dumps({'month': name, 'state': 'done', 'est_tokens': est, 'tokens_used': used,
                                                   'model_seconds': seconds}), encoding='utf-8')
        (d / 'receipts.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        return d

    def test_blocks_by_stage_once_each(self):
        d = self.month('2026-01', 10, 5, [row('scan_b', 'a', 'external'), row('scan_b', 'a', 'external'), row('scan_b', 'b', 'external'),
                                          row('read_a', 'c', 'external'), row('read_a', 'd', 'external', status='error'), row('verify', 'e', tokens=9)])
        self.assertEqual(receipts.external_blocks(d), {'scan_b': 2, 'read_a': 1})

    def test_none_without_agent_answers_or_records(self):
        d = self.month('2026-02', 10, 5, [row('scan_b', 'a', tokens=3)])
        self.assertEqual(receipts.external_blocks(d), {})
        self.assertEqual(receipts.external_blocks(self.root / 'nothing'), {})

    def test_a_changed_record_is_read_again(self):
        d = self.month('2026-03', 10, 5, [row('scan_b', 'a', tokens=3)])
        self.assertEqual(receipts.external_blocks(d), {})
        with open(d / 'receipts.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(row('scan_b', 'b', 'external')) + '\n')
        self.assertEqual(receipts.external_blocks(d), {'scan_b': 1})

    def test_estimate_scales_from_interface_months_only(self):
        self.month('2026-04', 100, 200, [row('scan_b', 'a', tokens=200)])            # ratio 2
        self.month('2026-05', 100, 50, [row('scan_b', 'a', 'external'), row('read_a', 'b', tokens=50)])   # agent did part
        self.assertEqual([r['month'] for r in pipeline.history(self.root)], ['2026-04'])
        got = pipeline.estimate(1000, {'concurrency': 1}, self.root)
        self.assertEqual(got['tokens'], 2000)
        self.assertEqual(got['basis'], '按 1 个已完成月份的实测')
        # Given the runs already read, the month asked about is still left out of its own estimate.
        runs = pipeline.history(self.root)
        self.assertEqual(pipeline.estimate(1000, {'concurrency': 1}, self.root, month='2026-04', runs=runs)['basis'], '按内置的初始估计')


if __name__ == '__main__':
    unittest.main()
