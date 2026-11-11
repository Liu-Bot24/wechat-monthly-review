"""The window's queue: estimates beside the job that calls models, and a run given to the external agent writing its
first tasks while another month runs. No processes are started: _start is replaced by a fake one."""
import tempfile, unittest
from pathlib import Path
from unittest import mock

from mr import config, jobs as jobs_mod
from mr.jobs import Jobs


class QueueTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.settings = {**config.load_settings(), 'output_root': str(base / 'out'), 'notify': False, 'external_stages': ['scan_b']}
        self.live, self.started = set(), []
        for p in (mock.patch.object(config, 'HOME', base / 'home'),
                  mock.patch.object(config, 'load_settings', lambda: self.settings),
                  mock.patch.object(jobs_mod, 'alive', lambda pid, created=None: pid in self.live)):
            p.start()
            self.addCleanup(p.stop)
        self.q = Jobs()
        self.q.close()                         # no watcher: the test moves the queue on itself
        self.q._kick = lambda: None

        def start(job):
            # A month that is running holds its lock: nothing else of that month starts.
            if any(j.get('month') == job['month'] for j in (self.q.data['current'], self.q.data['side']) if j):
                return False
            pid = 1000 + len(self.started)
            self.started.append(job)
            self.live.add(pid)
            job.update(pid=pid, started=0, log='')
            return True
        self.q._start = start

    def end(self, lane):
        self.live.discard(self.q.data[lane]['pid'])
        self.q._tick()

    def lanes(self):
        name = lambda j: j and (j['kind'], j['month'])
        return name(self.q.data['current']), name(self.q.data['side']), [name(j) for j in self.q.data['queue']]

    def test_an_estimate_does_not_wait_for_a_long_run(self):
        self.q.add([{'kind': 'run', 'month': '2026-09'}, {'kind': 'run', 'month': '2026-08'}])
        self.q._tick()
        self.q.add([{'kind': 'plan', 'month': '2026-07'}])
        self.q._tick()
        self.assertEqual(self.lanes(), (('run', '2026-09'), ('plan', '2026-07'), [('run', '2026-08')]))
        self.end('side')
        self.assertEqual(self.lanes(), (('run', '2026-09'), None, [('run', '2026-08')]))
        self.end('current')
        self.assertEqual(self.lanes(), (('run', '2026-08'), None, []))
        self.assertEqual([r['kind'] for r in self.q.data['history']], ['run', 'plan'])

    def test_a_waiting_job_takes_the_new_choice(self):
        self.q.add([{'kind': 'run', 'month': '2026-09'}])
        self.q._tick()
        self.q.add([{'kind': 'run', 'month': '2026-08', 'api_only': True}])
        added = self.q.add([{'kind': 'run', 'month': '2026-08', 'api_only': False}])
        self.assertEqual([j['api_only'] for j in added], [False])
        self.assertEqual([j['api_only'] for j in self.q.data['queue']], [False])
        self.assertEqual(self.q.add([{'kind': 'run', 'month': '2026-08', 'api_only': False}]), [])    # nothing new
        self.assertEqual(self.q.add([{'kind': 'run', 'month': '2026-09'}]), [])                       # that one is running

    def test_a_run_for_the_agent_hands_out_its_tasks_while_another_month_runs(self):
        from mr import server
        with mock.patch.object(server, 'jobs', self.q), mock.patch.object(server.config, 'missing_stages', return_value=[]):
            server.add_jobs({'jobs': [{'kind': 'run', 'month': '2026-09'}]})
            self.q._tick()
            self.assertEqual(self.q.data['current']['api_only'], True)               # not said: the models do it
            server.add_jobs({'jobs': [{'kind': 'run', 'month': '2026-08', 'agent': True}]})
            self.q._tick()
            self.assertEqual(self.lanes(), (('run', '2026-09'), ('plan', '2026-08'), [('run', '2026-08')]))
            self.assertEqual(jobs_mod.command(self.q.data['side']), ['plan', '2026-08', '--hand-out'])
            self.assertEqual(jobs_mod.command(self.q.data['queue'][0]), ['run', '2026-08'])
            self.end('side')
            self.end('current')
            self.assertEqual(self.lanes(), (('run', '2026-08'), None, []))
            # A month with tasks out already (waiting for a later batch): no estimate first, it would clear that batch.
            self.end('current')
            server.add_jobs({'jobs': [{'kind': 'run', 'month': '2026-09'}]})
            self.q._tick()
            from mr import external
            later = external.root(self.settings) / '精读' / '2026-06' / external.TASKS / 'esc-1.md'
            later.parent.mkdir(parents=True)
            later.write_text('任务', encoding='utf-8')
            server.add_jobs({'jobs': [{'kind': 'run', 'month': '2026-06', 'agent': True}]})
            self.assertEqual(self.lanes(), (('run', '2026-09'), None, [('run', '2026-06')]))
            self.q.data['queue'].clear()
            # Nothing else running: the run writes its tasks itself, no estimate first.
            self.end('current')
            server.add_jobs({'jobs': [{'kind': 'run', 'month': '2026-07', 'agent': True}]})
            self.q._tick()
            self.assertEqual(self.lanes(), (('run', '2026-07'), None, []))
            # Given to the agent with no stage ticked for it in the settings: refused, and said why.
            self.settings['external_stages'] = []
            with self.assertRaises(server.HTTPException) as e:
                server.add_jobs({'jobs': [{'kind': 'run', 'month': '2026-06', 'agent': True}]})
            self.assertIn('外部 Agent', e.exception.detail)

    def test_the_hand_out_estimate_is_dropped_once_its_run_starts(self):
        self.q.add([{'kind': 'run', 'month': '2026-09'}])
        self.q._tick()
        self.q.add([{'kind': 'plan', 'month': '2026-05'}, {'kind': 'plan', 'month': '2026-08', 'hand_out': True},
                    {'kind': 'run', 'month': '2026-08'}])
        self.q._tick()                         # the side lane is busy with 2026-05's estimate
        self.end('current')                    # 2026-08's run starts before its estimate did: the run writes the tasks
        self.assertEqual(self.lanes(), (('run', '2026-08'), ('plan', '2026-05'), []))


if __name__ == '__main__':
    unittest.main()
