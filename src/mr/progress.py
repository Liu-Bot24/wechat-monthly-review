"""Live progress of one run, written to <run_dir>/progress.json for the window to show.

Writes are atomic and at most about once a second; stage changes, the end of a stage and events write at once.
"""
import json, os, threading, time
from pathlib import Path

# Run order. weight: rough share of a typical run's time, used for the overall bar and the time left.
STEPS = [
    ('prepare', '取数与语音转写', 0.06),
    ('read', '精读和扫读', 0.58),
    ('escalate', '申请精读', 0.01),
    ('recall', '查漏', 0.16),
    ('verify', '结论卡复核', 0.08),
    ('vision', '看图', 0.03),
    ('at_me', '@我', 0.01),
    ('compose', '成稿', 0.06),
    ('assemble', '写出报告', 0.01),
]

class Progress:
    def __init__(self, path, month, kind='run', steps=None):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.last_write = 0.0
        self.state = {
            'month': month, 'kind': kind, 'state': 'running', 'pid': os.getpid(), 'started': time.time(), 'updated': time.time(),
            'step': None, 'message': '',
            'steps': [{'key': k, 'label': l, 'weight': w, 'status': 'pending', 'done': 0, 'total': 0, 'failed': 0,
                       'started': None, 'ended': None} for k, l, w in (steps or STEPS)],
            'tokens': 0, 'calls': 0, 'budget': None, 'events': [],
        }
        self.write(force=True)

    def _step(self, key):
        return next(s for s in self.state['steps'] if s['key'] == key)

    def write(self, force=False):
        now = time.time()
        if not force and now - self.last_write < 1.0:
            return
        self.last_write = now
        self.state['updated'] = now
        self.state['fraction'], self.state['eta_seconds'] = self._estimate(now)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
        try:
            tmp.replace(self.path)
        except PermissionError:      # the window is reading it right now; the next write will land
            pass

    def _estimate(self, now):
        """Overall fraction done, weighted by step; time left from the pace so far."""
        done = 0.0
        for s in self.state['steps']:
            if s['status'] in ('done', 'skipped'):
                done += s['weight']
            elif s['status'] == 'running' and s['total']:
                done += s['weight'] * min(1.0, s['done'] / s['total'])
        total = sum(s['weight'] for s in self.state['steps'])
        fraction = done / total
        elapsed = now - self.state['started']
        eta = elapsed * (1 - fraction) / fraction if fraction > 0.03 and elapsed > 20 else None
        # Inside a big step, its own pace is a better guide than the run's.
        cur = next((s for s in self.state['steps'] if s['status'] == 'running'), None)
        if cur and cur['total'] and cur['done'] >= 5 and cur['started']:
            pace = (now - cur['started']) / cur['done']
            rest_here = pace * (cur['total'] - cur['done'])
            rest_weight = sum(s['weight'] for s in self.state['steps'] if s['status'] == 'pending')
            per_weight = (now - cur['started']) / max(1e-6, cur['weight'] * cur['done'] / cur['total'])
            eta = rest_here + rest_weight * per_weight
        return round(fraction, 4), (int(eta) if eta is not None else None)

    def begin(self, key, total=0, message='', label=None):
        """label: what this step is called in this run, when it does only part of what its name says."""
        with self.lock:
            s = self._step(key)
            s.update(status='running', total=total, done=0, failed=0, started=time.time())
            if label:
                s['label'] = label
            self.state['step'], self.state['message'] = key, message or s['label']
            self.write(force=True)

    def tick(self, key, ok=True):
        with self.lock:
            s = self._step(key)
            s['done'] += 1
            s['failed'] += 0 if ok else 1
            self.write()

    def recover(self, key, n):
        """n jobs that had failed in this step succeeded on a later try."""
        with self.lock:
            s = self._step(key)
            s['failed'] = max(0, s['failed'] - n)
            self.write()

    def end(self, key, status='done', message=None):
        with self.lock:
            s = self._step(key)
            s.update(status=status, ended=time.time())
            if s['total'] == 0 and status == 'done':
                s['done'] = 0
            if message:
                self._event(message)
            self.write(force=True)

    def skip(self, key, reason=''):
        self.end(key, 'skipped', f'{self._step(key)["label"]}：跳过{("，" + reason) if reason else ""}')

    def message(self, text):
        with self.lock:
            self.state['message'] = text
            self.write(force=True)

    def tokens(self, used, calls):
        with self.lock:
            self.state['tokens'], self.state['calls'] = used, calls
            self.write()

    def _event(self, text):
        self.state['events'].append({'at': time.time(), 'text': text})
        del self.state['events'][:-60]

    def event(self, text):
        with self.lock:
            self._event(text)
            self.write(force=True)

    def finish(self, state, message=''):
        with self.lock:
            self.state['state'], self.state['message'] = state, message
            for s in self.state['steps']:
                if s['status'] == 'running':
                    s['status'] = 'stopped' if state != 'done' else 'done'
            if message:
                self._event(message)
            self.write(force=True)


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
