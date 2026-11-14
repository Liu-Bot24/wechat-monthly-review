"""Runs started from the window: a queue, history, and the per-month lock.

Two lanes: one job that calls models at a time, and beside it estimates, which call none (they only read the archive
and may write an external agent's first tasks). So an estimate, or the tasks of a month given to an agent, does not
wait for a long run of another month.

Each run is its own process (python -m mr ...), so closing or restarting the window does not stop it; the window
reattaches through jobs.json and the run's progress.json. Stopping ends the process tree; every answered call is in
the cache, so continuing later redoes nothing.
"""
import ctypes, json, os, re, subprocess, sys, threading, time, uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from . import FROZEN, ROOT, config
from .progress import read as read_progress

KINDS = {'run': '生成月报', 'plan': '估算', 'asr': '语音转写', 'topic-plan': '专题预览', 'topic-screen': '专题筛查', 'topic-run': '生成专题'}
# A 专题 job's month is "专题/<id>", so its lock, progress and log sit in the 专题's own folder.
TOPIC_KINDS = ('topic-plan', 'topic-screen', 'topic-run')
FINAL = ('done', 'done-with-problems', 'budget-stop', 'external-wait', 'failed', 'stopped')
LIGHT = ('plan', 'topic-plan')          # no model calls: they run in the second lane
LANES = ('current', 'side')

# ---------- processes ----------
_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

def _created(pid):
    """Creation time of a live process (FILETIME as int), or None when it is not running."""
    handle = _k32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        _k32.CloseHandle(handle)

def alive(pid, created=None):
    """Whether pid still runs and, when created is given, is the same process (pids are reused)."""
    if not pid:
        return False
    now = _created(pid)
    return now is not None and (created is None or now == created)

def kill_tree(pid):
    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

# ---------- locks ----------
class Busy(Exception):
    pass

def _lock_path(output_root, month):
    return Path(output_root) / month / '.lock' if month != 'asr' else Path(output_root) / '语音转写' / '.lock'

def lock_holder(output_root, month):
    """The live process holding a month's lock, or None (a stale lock counts as free)."""
    path = _lock_path(output_root, month)
    try:
        held = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return held if alive(held.get('pid'), held.get('created')) else None

@contextmanager
def month_lock(output_root, month):
    """One run per month at a time, whether started from the window or the command line."""
    path = _lock_path(output_root, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    held = lock_holder(output_root, month)
    if held and held['pid'] != os.getpid():
        raise SystemExit(f'{month} 已经在运行（进程 {held["pid"]}，{held.get("started", "")} 开始）。')
    path.write_text(json.dumps({'pid': os.getpid(), 'created': _created(os.getpid()),
                                'started': time.strftime('%Y-%m-%d %H:%M:%S')}), encoding='utf-8')
    try:
        yield
    finally:
        try:
            path.unlink()
        except OSError:
            pass

# ---------- notifications ----------
_TOAST = r'''
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$t = [Security.SecurityElement]::Escape($env:MR_TOAST_TITLE); $b = [Security.SecurityElement]::Escape($env:MR_TOAST_BODY)
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
'''

def notify(title, body):
    """A Windows notification; failures are ignored because the window shows the same result."""
    try:
        env = {**os.environ, 'MR_TOAST_TITLE': title, 'MR_TOAST_BODY': body[:240]}
        subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', _TOAST], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        pass

# ---------- the queue ----------
def _mr():
    """How to start `mr ...`: the installed console program beside the window's, or this Python with the repository's
    launcher. The launcher finds the package by itself, so the command also works where it is pasted: in an agent's
    shell, in any folder."""
    exe = Path(sys.executable)
    if FROZEN:
        return [str(exe.with_name('mr.exe'))]
    if exe.name.lower() == 'pythonw.exe' and (exe.parent / 'python.exe').exists():
        exe = exe.parent / 'python.exe'
    return [str(exe), str(ROOT / 'tools' / 'run_mr.py')]

def progress_path(output_root, job):
    if job['kind'] == 'asr':
        return Path(output_root) / '语音转写' / 'progress.json'
    # An estimate is short and writes no progress of its own; it must not clear the last run's.
    return Path(output_root) / job['month'] / ('plan-progress.json' if job['kind'] in ('plan', 'topic-plan') else 'progress.json')

def log_path(output_root, job):
    if job['kind'] == 'asr':
        return Path(output_root) / '语音转写' / 'asr.log'
    return Path(output_root) / job['month'] / ('plan.log' if job['kind'] in ('plan', 'topic-plan') else 'run.log')

def command(job):
    api_only = ['--api-only'] if job.get('api_only') else []
    hand_out = ['--hand-out'] if job.get('hand_out') else []
    if job['kind'] == 'topic-plan':
        return ['topic', 'plan', job['topic']] + hand_out
    if job['kind'] in TOPIC_KINDS:
        return ['topic', job['kind'][6:], job['topic']] + (['--budget-tokens', str(job['budget'])] if job.get('budget') else []) + api_only
    if job['kind'] == 'run':
        return ['run', job['month']] + (['--budget-tokens', str(job['budget'])] if job.get('budget') is not None else []) + api_only
    if job['kind'] == 'plan':
        return ['plan', job['month']] + hand_out
    args = ['asr', '--from', job['from'], '--to', job['to']]
    for chat in job.get('chats') or []:
        args += ['--chat', chat]
    return args


class Jobs:
    """The window's queue. data['current'] is the job calling models, data['side'] an estimate beside it; a watcher
    thread starts the next job of each lane and records the finished ones."""

    def __init__(self, on_change=None):
        self.path = config.HOME / 'jobs.json'
        self.lock = threading.RLock()
        self.on_change = on_change
        self.on_idle = None           # called every few seconds by the watcher: what should go on by itself is queued there
        self.data = self._load()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data.setdefault('queue', []); data.setdefault('history', []); data.setdefault('current', None); data.setdefault('side', None)
        for r in data['history']:
            # Older versions ended with 校验提示 and left them; results are fixed when shown (pipeline.settle).
            if '；校验提示' in (r.get('message') or ''):
                r['message'] = re.sub(r'；校验提示 \d+ 条', '', r['message'])
                if r.get('state') == 'done-with-problems' and '块失败' not in r['message']:
                    r['state'] = 'done'
        return data

    def _save(self):
        config.HOME.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)
        if self.on_change:
            self.on_change()

    def snapshot(self):
        with self.lock:
            out = json.loads(json.dumps(self.data))
        root = config.load_settings()['output_root']
        for lane in LANES:
            if out[lane]:
                out[lane]['progress'] = read_progress(progress_path(root, out[lane]))
        return out

    # ----- queue edits -----
    def add(self, jobs):
        """jobs: [{'kind', 'month', 'budget'?, 'api_only'?, 'hand_out'?} | {'kind': 'asr', 'from', 'to', 'chats'?}].
        A job like one already waiting is not added twice: the waiting one takes the new choices (models or the agent,
        the budget, handing out tasks) and counts as added when they change it. One like a job running now is dropped."""
        added = []
        with self.lock:
            for j in jobs:
                if j['kind'] not in KINDS:
                    raise ValueError(f'未知的任务类型 {j["kind"]}')
                same = lambda o: o['kind'] == j['kind'] and o.get('month') == j.get('month') and j['kind'] != 'asr'
                waiting = next((o for o in self.data['queue'] if same(o)), None)
                if waiting:
                    # Asking for it again also takes it off pause.
                    before = {k: waiting.get(k) for k in ('api_only', 'budget', 'hand_out', 'paused')}
                    waiting.update({k: j[k] for k in ('api_only', 'budget') if k in j})
                    waiting['hand_out'] = bool(waiting.get('hand_out') or j.get('hand_out'))
                    waiting.pop('paused', None)
                    if {k: waiting.get(k) for k in before} != before:
                        added.append(waiting)
                    continue
                # A running estimate that does not hand out tasks does not stand in for one that does.
                if any(self.data[lane] and same(self.data[lane]) and (self.data[lane].get('hand_out') or not j.get('hand_out'))
                       for lane in LANES):
                    continue
                job = {**j, 'id': uuid.uuid4().hex[:10], 'added': time.time()}
                if j['kind'] == 'asr':
                    job['month'] = 'asr'
                self.data['queue'].append(job)
                added.append(job)
            self._save()
        self._kick()
        return added

    def remove(self, job_id):
        with self.lock:
            self.data['queue'] = [j for j in self.data['queue'] if j['id'] != job_id]
            self._save()

    def move(self, job_id, delta):
        with self.lock:
            q = self.data['queue']
            i = next((n for n, j in enumerate(q) if j['id'] == job_id), None)
            if i is None:
                return
            k = max(0, min(len(q) - 1, i + delta))
            q.insert(k, q.pop(i))
            self._save()

    def busy(self):
        """Whether a job that calls models is running or waiting: one added now that calls them would wait for it."""
        with self.lock:
            return bool(self.data['current']) or any(j['kind'] not in LIGHT and not j.get('paused') for j in self.data['queue'])

    def pause(self, job_id):
        """A running job ends like a stop (its answered calls are cached) and goes back to the head of the queue, paused;
        a waiting one is held where it is. A paused job keeps its place and starts only once resumed."""
        with self.lock:
            cur = next((self.data[lane] for lane in LANES if self.data[lane] and self.data[lane]['id'] == job_id), None)
            if cur:
                cur['pause_requested'] = True
                self._save()
            else:
                for j in self.data['queue']:
                    if j['id'] == job_id:
                        j['paused'] = True
                        self._save()
        if cur and alive(cur['pid'], cur.get('created')):
            kill_tree(cur['pid'])
        self._kick()

    def resume(self, job_id):
        with self.lock:
            for j in self.data['queue']:
                if j['id'] == job_id:
                    j.pop('paused', None)
                    self._save()
        self._kick()

    def stop(self, clear_queue=False, job_id=None):
        """End a running job's process tree: the one with this id, else the one calling models. Its answered calls
        are cached, so it can continue later."""
        with self.lock:
            cur = self.data['current']
            if job_id:
                cur = next((self.data[lane] for lane in LANES if self.data[lane] and self.data[lane]['id'] == job_id), None)
            if clear_queue:
                self.data['queue'] = []
            if cur:
                cur['stop_requested'] = True
            self._save()
        if cur and alive(cur['pid'], cur.get('created')):
            kill_tree(cur['pid'])
        self._kick()

    # ----- running -----
    def _kick(self):
        threading.Thread(target=self._tick, daemon=True).start()

    def _start(self, job):
        settings = config.load_settings()
        root = settings['output_root']
        if lock_holder(root, job['month']):
            return False
        log = log_path(root, job)
        log.parent.mkdir(parents=True, exist_ok=True)
        prog = progress_path(root, job)
        try:
            prog.unlink()                 # a stale file from the last run must not look like this one
        except OSError:
            pass
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
               'MR_HOME': str(config.HOME)}
        if FROZEN:
            env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'     # mr.exe is its own frozen program, not a child of this one
        with log.open('a', encoding='utf-8') as f:
            f.write(f'\n===== {time.strftime("%Y-%m-%d %H:%M:%S")} {KINDS[job["kind"]]} {" ".join(command(job))} =====\n')
        out = log.open('a', encoding='utf-8')
        proc = subprocess.Popen(_mr() + command(job), cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        out.close()
        job.update(pid=proc.pid, created=_created(proc.pid), started=time.time(), log=str(log))
        self._procs = {pid: p for pid, p in getattr(self, '_procs', {}).items() if p.poll() is None}
        self._procs[proc.pid] = proc
        return True

    def _tick(self):
        with self.lock:
            for lane in LANES:
                cur = self.data[lane]
                if cur and not alive(cur['pid'], cur.get('created')):
                    self._finish(cur, lane)
            for lane in LANES:
                if self.data[lane]:
                    continue
                for i, job in enumerate(self.data['queue']):
                    if job.get('paused') or (job['kind'] in LIGHT) != (lane == 'side') or not self._start(job):
                        continue
                    self.data[lane] = self.data['queue'].pop(i)
                    if lane == 'current':
                        # The run writes its tasks itself now: an estimate queued only to write them first is not needed.
                        self.data['queue'] = [o for o in self.data['queue'] if not (o.get('hand_out') and o.get('month') == job.get('month'))]
                    self._save()
                    break

    def _finish(self, job, lane='current'):
        root = config.load_settings()['output_root']
        prog = read_progress(progress_path(root, job)) or {}
        proc = getattr(self, '_procs', {}).pop(job['pid'], None)
        code = proc.poll() if proc is not None else None
        state = prog.get('state')
        if job.get('stop_requested'):
            state = 'stopped'
        elif job.get('pause_requested') and state not in FINAL:
            state = 'paused'            # one that ended by itself just before keeps its own end
        elif job['kind'] == 'plan':
            state = 'done' if code in (0, None) and (Path(root) / job['month'] / 'plan.json').exists() else 'failed'
        elif state not in FINAL:
            state = 'failed'
        message = prog.get('message') or ''
        if state == 'failed' and not message:
            message = _last_lines(job.get('log'))
        if prog and prog.get('state') == 'running':
            # The process ended without writing its own end (stopped, or killed); say so in its progress file.
            prog.update(state=state, message=message or {'stopped': '已停止', 'paused': '已暂停'}.get(state, '进程意外结束'))
            for s in prog.get('steps', []):
                if s['status'] == 'running':
                    s['status'] = 'stopped'
            path = progress_path(root, job)
            path.with_suffix('.tmp').write_text(json.dumps(prog, ensure_ascii=False), encoding='utf-8')
            path.with_suffix('.tmp').replace(path)
        record = {k: job.get(k) for k in ('id', 'kind', 'month', 'budget', 'api_only', 'hand_out', 'from', 'to', 'chats', 'topic', 'title', 'started', 'log')}
        record.update(ended=time.time(), state=state, message=message[:600], tokens=prog.get('tokens'), calls=prog.get('calls'),
                      report=prog.get('report'))
        self.data['history'].insert(0, record)
        del self.data['history'][300:]
        self.data[lane] = None
        if state == 'paused':
            # Back to the head of the queue as a new job, holding its place until resumed.
            again = {k: v for k, v in job.items() if k not in ('pid', 'created', 'started', 'log', 'pause_requested', 'progress', 'handoff')}
            self.data['queue'].insert(0, {**again, 'id': uuid.uuid4().hex[:10], 'paused': True})
        self._save()
        if config.load_settings().get('notify') and job['kind'] not in ('plan', 'topic-plan') and state not in ('stopped', 'paused'):
            what = (f'{job["month"]} 月报' if job['kind'] == 'run' else '语音转写' if job['kind'] == 'asr'
                    else f'专题「{job.get("title") or job.get("topic")}」' + ('筛查' if job['kind'] == 'topic-screen' else ''))
            title = {'done': f'{what}已完成', 'done-with-problems': f'{what}有部分失败', 'budget-stop': f'{what}：已到预算上限，暂停',
                     'external-wait': f'{what}：等待外部 Agent',
                     'failed': f'{what}失败'}.get(state, what)
            notify(title, message or '')

    def _watch(self):
        last = 0.0
        while not self._stop.wait(2.0):
            try:
                self._tick()
                if self.on_idle and time.time() - last >= 6:
                    last = time.time()
                    self.on_idle()
            except Exception as e:        # the watcher must keep going; the error is visible in the window log
                print('jobs watcher:', e, file=sys.stderr)

    def close(self):
        self._stop.set()


def _last_lines(log, n=6):
    try:
        lines = Path(log).read_text(encoding='utf-8', errors='replace').strip().splitlines()
    except (OSError, TypeError):
        return ''
    return '\n'.join(lines[-n:])
