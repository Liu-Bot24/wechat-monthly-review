"""The window's local service: JSON API under /api and the page under /. Listens on 127.0.0.1 only.

Every API request must carry the X-MR-Token header the page was served with, so other web pages open in a browser
cannot drive it (a cross-origin request with a custom header needs a preflight this server never grants).
"""
import io, json, os, re, secrets, subprocess, sys, threading, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from . import PROGRAM, ROOT, __version__, config, external, receipts, source, voice
from .archive import Archive, TZ, ancestors, location_problem, media_id, media_kind, media_path, month_bounds, stamp
from . import topic
from .jobs import LANES, TOPIC_KINDS, Jobs, _mr, lock_holder
from .media import read_transcript, voice_pace
from .pipeline import FINGERPRINT_LABELS, fingerprint, settle, system_prompts
from .progress import read as read_json
from .selection import Selection

UI = Path(__file__).resolve().parent / 'ui'
TOKEN = secrets.token_urlsafe(24)
BROWSER_IMAGES = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.avif'}

app = FastAPI(title='消息月报', docs_url=None, redoc_url=None, openapi_url=None)
_archive = {'obj': None, 'root': None}
_alock = threading.RLock()
_cache = {}
jobs = None


# ---------- plumbing ----------
@app.middleware('http')
async def guard(request: Request, call_next):
    if request.url.path.startswith('/api/') and request.headers.get('x-mr-token') != TOKEN \
            and request.query_params.get('t') != TOKEN:
        return JSONResponse({'error': '未授权的请求'}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/ui/') and 'cache-control' not in response.headers:
        # Revalidate every load (cheap on this computer), so an update never runs with an old script.
        response.headers['Cache-Control'] = 'no-cache'
    return response

@app.exception_handler(config.ConfigError)
async def config_error(request, e):
    return JSONResponse({'error': str(e)}, status_code=400)

@app.exception_handler(HTTPException)
async def http_error(request, e):
    return JSONResponse({'error': e.detail}, status_code=e.status_code)

def fail(status, message):
    raise HTTPException(status, message)

def archive():
    """The archive, opened once and reopened when the archive or report folder setting changes. The first look at a large
    archive reads every document once, in the background; until then requests are answered with how far it has got."""
    s = config.load_settings()
    where = (s['archive_root'], s['output_root'])
    with _alock:
        if _archive['obj'] is not None and _archive['root'] == where:
            return _archive['obj']
        problem = location_problem(where[0])
        if problem:
            fail(409, problem)
        error = _archive.pop('error', None)
        if error:                                # reported once; the next request tries again
            fail(500, f'读取归档目录失败：{error}')
        if _archive.get('building') != where:
            _archive.update(building=where, progress=(0, 0))
            threading.Thread(target=_open_archive, args=(where,), daemon=True).start()
        done, total = _archive['progress']
    fail(503, '正在读取归档目录中的聊天记录' + (f'：{done:,} / {total:,} 个文件' if total else '') + '。第一次需要几分钟，之后只读有变化的文件。')

def _open_archive(where):
    try:
        a = Archive(*where, progress=lambda done, total: _archive.update(progress=(done, total)))
    except BaseException as e:
        print(f'归档读取失败：{e}', flush=True)
        with _alock:
            _archive.update(building=None, error=str(e) or type(e).__name__)
        return
    with _alock:
        if _archive.get('building') == where:
            _archive.update(obj=a, root=where, building=None)
            _cache.clear()

def selection():
    return Selection(config.selection_path(), config.load_settings()['default_skip_categories'])

def month_list(first, last):
    out, y, m = [], int(first[:4]), int(first[5:])
    while f'{y:04d}-{m:02d}' <= last:
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out

def safe_path(path):
    """Only files under the archive, the output folder or the user config may be opened or served."""
    s = config.load_settings()
    p = Path(path).resolve()
    for base in (s['archive_root'], s['output_root'], config.HOME, s.get('external_root')):
        if not base:                          # an unset folder would resolve to the working directory
            continue
        try:
            p.relative_to(Path(base).resolve())
            return p
        except ValueError:
            continue
    fail(403, '只能打开归档、月报输出目录、交接文件夹和程序配置中的文件')


# ---------- page ----------
@app.get('/', response_class=HTMLResponse)
def index():
    html = (UI / 'index.html').read_text(encoding='utf-8')
    # Tag scripts and styles with their own change time, so a window never keeps an old copy after an update.
    html = re.sub(r'"/ui/([^"?]+\.(?:js|css))"', lambda m: f'"/ui/{m.group(1)}?v={int((UI / m.group(1)).stat().st_mtime)}"'
                  if (UI / m.group(1)).exists() else m.group(0), html)
    return html.replace('__MR_TOKEN__', TOKEN).replace('__MR_VERSION__', __version__)

app.mount('/ui', StaticFiles(directory=UI), name='ui')


# ---------- overview ----------
def _first_month():
    if 'first' not in _cache:
        row = archive().earliest()
        _cache['first'] = stamp(row).strftime('%Y-%m') if row else datetime.now(TZ).strftime('%Y-%m')
    return _cache['first']

def current_fingerprint():
    s = config.load_settings()
    return fingerprint(system_prompts(ROOT), config.load_models(), selection(), s)

def _source_changed(month, run_dir, st):
    """The one reason a finished report goes out of date: the archive now holds clearly more (or fewer) messages for
    that month than when it was written, after a later import. Prompt, model or selection changes do not count;
    regenerating for those is my choice, not something to nag about."""
    a = archive()
    with _alock:
        now = a.month_messages(*month_bounds(month))
    then = st.get('source_messages')
    if then is None:
        # Reports written before this count was kept: take today's archive as their baseline.
        st['source_messages'] = now
        tmp = run_dir / 'status.tmp'
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(run_dir / 'status.json')
        return None
    if source.big_enough(then, now):
        return f'数据源有变化：本月消息 {then:,} → {now:,} 条'
    return None

def _status_file(month):
    if not re.match(r'^\d{4}-\d{2}$', month):
        fail(400, '月份格式应为 YYYY-MM')
    run_dir = Path(config.load_settings()['output_root']) / month
    st = read_json(run_dir / 'status.json')
    if not st or not (run_dir / f'月报_{month}.md').exists():
        fail(404, '这个月份还没有月报')
    return run_dir, st

@app.get('/api/months/{month}/source')
def month_source(month: str):
    """What changed in the archive for this month since its report was written."""
    run_dir, st = _status_file(month)
    a = archive()
    with _alock:
        return source.detail(a, selection(), month, st, read_json(run_dir / 'progress.json'),
                             (run_dir / f'月报_{month}.md').read_text(encoding='utf-8'))

@app.post('/api/months/{month}/source/accept')
def accept_source(month: str):
    """Keep the report as it is: the archive as it stands now becomes what later changes are measured from."""
    run_dir, st = _status_file(month)
    if lock_holder(config.load_settings()['output_root'], month):
        fail(409, '这个月份正在运行，结束后再操作。')
    a = archive()
    with _alock:
        source.accept(a, month, st)
    tmp = run_dir / 'status.tmp'
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(run_dir / 'status.json')
    return {'ok': True}

def _settle(run_dir, month):
    """Fix a result an older version left with 校验提示 (see pipeline.settle), unless a run holds it now."""
    try:
        if not lock_holder(config.load_settings()['output_root'], month):
            settle(run_dir)
    except OSError as e:
        print(f'整理 {month} 的校验提示时出错：{e}', file=sys.stderr)

def month_state(month, cutoff, fp, snap):
    s = config.load_settings()
    run_dir = Path(s['output_root']) / month
    _settle(run_dir, month)
    start, end = month_bounds(month)
    # Whether the archive holds all of a month is the user's call; a month is ready once it has ended and has messages.
    data = 'none' if start >= cutoff else 'complete' if end <= time.time() else 'partial'
    st = read_json(run_dir / 'status.json') or {}
    report = run_dir / f'月报_{month}.md'
    out = {'month': month, 'data': data, 'state': st.get('state') or ('done' if report.exists() else None),
           'report': report.exists(), 'tokens': st.get('tokens_used'), 'updated': st.get('updated'), 'stale': []}
    plan = read_json(run_dir / 'plan.json')
    out['estimate'] = plan.get('estimate') if plan else None
    if out['state'] in ('done', 'done-with-problems') and not out['report']:
        out['state'] = 'missing'       # finished once, but the report file has since been moved or deleted
    if out['report'] and st and st.get('state') != 'running':
        reason = _source_changed(month, run_dir, st)
        out['stale'] = [reason] if reason else []
    cur = next((snap[lane] for lane in LANES if (snap[lane] or {}).get('month') == month), None)
    if cur:
        out['job'] = cur['kind']
    elif any(j.get('month') == month for j in snap['queue']):
        out['job'] = 'queued'
    return out

@app.get('/api/overview')
def overview():
    a = archive()
    cutoff = a.latest()
    last = stamp(cutoff).strftime('%Y-%m')
    fp, snap = current_fingerprint(), jobs.snapshot()
    months = [month_state(m, cutoff, fp, snap) for m in month_list(_first_month(), last)]
    done = [m for m in months if m['tokens'] and m['state'] in ('done', 'done-with-problems', 'missing')]
    recent = sorted(done, key=lambda m: m['month'])[-3:]
    avg = sum(m['tokens'] for m in recent) / len(recent) if recent else None
    return {'cutoff': cutoff, 'cutoff_text': stamp(cutoff - 1).strftime('%Y-%m-%d %H:%M'), 'months': months,
            'average_tokens': avg, 'suggested_budget': int(round(avg * 2.25, -5)) if avg else None,
            'budget': config.load_settings()['budget_tokens'], 'missing_stages': [s['label'] for s in config.missing_stages(config.load_models(), config.load_settings())]}

@app.get('/api/months/{month}/plan')
def month_plan(month: str):
    plan = read_json(Path(config.load_settings()['output_root']) / month / 'plan.json')
    if not plan:
        return {'plan': None}
    fp = current_fingerprint()
    # Only what changes the counts and the estimate; prompt and model changes do not.
    plan['stale'] = [FINGERPRINT_LABELS[k] for k in ('selection', 'settings') if plan.get('fingerprint', {}).get(k) != fp.get(k)]
    return {'plan': plan}

@app.get('/api/months/{month}/masks')
def month_masks(month: str):
    return _masks(Path(config.load_settings()['output_root']) / month / 'masks.json')

def _masks(path):
    """Each masked value with its original, read back from the archive for me; models never see these, and nothing is saved."""
    from .extract import masked_values
    rows = read_json(path)
    if rows is None:
        return {'items': None}
    a = archive()
    items = []
    with _alock:
        for r in rows:
            hit = a.message(r['doc'], r['anchor']) if r.get('anchor') else None    # rows from before the Markdown archive have no anchor
            values = masked_values(a, hit) if hit else []
            for i, label in enumerate(r['labels']):
                items.append({'label': label, 'value': values[i] if i < len(values) else None, 'lookup_id': r['lookup_id'],
                              'name': r['name'], 'time': stamp(r['ts']).strftime('%Y-%m-%d %H:%M'), 'self': r['self'], 'in_month': r['in_month']})
    return {'items': items}

def recycle(paths):
    """Move files and folders to the Windows Recycle Bin (never a permanent delete)."""
    import ctypes
    from ctypes import wintypes
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', ctypes.c_void_p), ('pTo', ctypes.c_void_p),
                    ('fFlags', ctypes.c_ushort), ('fAnyOperationsAborted', wintypes.BOOL), ('hNameMappings', ctypes.c_void_p),
                    ('lpszProgressTitle', wintypes.LPCWSTR)]
    if not paths:
        return
    names = ctypes.create_unicode_buffer('\0'.join(str(Path(p).resolve()) for p in paths) + '\0\0')
    # FO_DELETE with FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    op = SHFILEOPSTRUCTW(None, 3, ctypes.addressof(names), None, 0x40 | 0x10 | 0x400 | 0x4, False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code or op.fAnyOperationsAborted:
        raise OSError(f'移到回收站失败（代码 {code}）')

# Deleting a result removes its call records with it: the usage page shows what exists now, not what once did.
# Only when a month's cache is kept do its records stay beside it (out of the usage page until the month is
# generated again), because a report written from that cache was paid for by those calls.

@app.delete('/api/months/{month}')
def delete_month(month: str, cache: int = 0):
    if not re.match(r'^\d{4}-\d{2}$', month):
        fail(400, '月份格式应为 YYYY-MM')
    s = config.load_settings()
    run_dir = Path(s['output_root']) / month
    if not run_dir.is_dir():
        fail(404, '这个月份没有可以删除的文件')
    snap = jobs.snapshot()
    if any((snap[lane] or {}).get('month') == month for lane in LANES) or any(j.get('month') == month for j in snap['queue']) or lock_holder(s['output_root'], month):
        fail(409, '这个月份有任务正在运行或排队。先停止任务或把它移出队列，再删除。')
    keep = {'cache', 'receipts.jsonl'} if not cache and (run_dir / 'cache').is_dir() else set()
    items = [p for p in run_dir.iterdir() if p.name not in keep]
    if _checks_path(month).exists():
        items.append(_checks_path(month))
    try:
        recycle(items)
    except OSError as e:
        fail(500, str(e))
    return {'ok': True, 'removed': len(items)}

@app.get('/api/months/{month}/status')
def month_status(month: str):
    d = Path(config.load_settings()['output_root']) / month
    _settle(d, month)
    return {'status': read_json(d / 'status.json'), 'progress': read_json(d / 'progress.json'), 'handoff': _handoff(month),
            'plan': read_json(d / 'plan.json'), 'locked': bool(lock_holder(config.load_settings()['output_root'], month))}

def _handoff(month):
    """What waits for an external agent in a month's or 专题's handoff folder: how many tasks, how many results have
    come back, and the prompt to give the agent. None when nothing waits. It is read from the folder itself, so it
    is there from the first task written, whatever state the run is in."""
    s = config.load_settings()
    found = external.owner_tally(s, month)
    if not found['tasks']:
        return None
    # Per stage, for the prompt maker: the stages handed out in the settings (tasks may come for them later), and any
    # other that has tasks here now.
    counts = {st['name']: st for st in found['stages']}
    names = [external.MONTHLY[k][0] for k in config.STAGE_KEYS if k in (s.get('external_stages') or [])]
    names += [n for n in counts if n not in names]
    return {'tasks': found['tasks'], 'results': found['results'], 'folder': str(external.root(s)),
            'stages': [{'name': n, 'tasks': counts.get(n, {}).get('tasks', 0), 'results': counts.get(n, {}).get('results', 0)} for n in names]}

@app.post('/api/external/prompt')
def external_prompt(body: dict = Body(...)):
    """The prompt for one agent: it covers the stage folders named (by the names the settings show), whichever they are."""
    known = {name for name, _ in external.MONTHLY.values()}
    names = [n for n in body.get('stages') or [] if n in known]
    if not names:
        fail(400, '请至少选择一个环节')
    base = external.root(config.load_settings())
    return {'prompt': external.prompt([base / n for n in names], _mr())}

_resumed = {}       # month -> the results it was last continued for, so that one set of results continues it once

def _resume_waiting():
    """A month or 专题 that stopped to wait for an external agent goes on by itself once every result is in."""
    s = config.load_settings()
    out = Path(s['output_root'])
    for status in list(out.glob('[0-9][0-9][0-9][0-9]-[0-9][0-9]/status.json')) + list(topic.root_dir(s).glob('*/status.json')):
        d = status.parent
        month = d.name if d.parent == out else f'专题/{d.name}'
        if (read_json(status) or {}).get('state') != 'external-wait' or lock_holder(s['output_root'], month):
            continue
        found = external.owner_tally(s, month)
        if not found['tasks'] or found['results'] < found['tasks'] or _resumed.get(month) == found['files']:
            continue
        _resumed[month] = found['files']
        try:
            add_jobs({'jobs': [{'kind': 'topic-run', 'topic': d.name, 'agent': True} if d.parent != out else {'kind': 'run', 'month': month, 'agent': True}]})
        except HTTPException as e:
            print(f'{month} 的结果交齐了，但没能自动继续：{e.detail}', file=sys.stderr)


# ---------- jobs ----------
@app.get('/api/jobs')
def get_jobs():
    snap = jobs.snapshot()
    cur = snap['current']
    if cur and cur.get('kind') in ('run', 'topic-run', 'topic-screen'):
        cur['handoff'] = _handoff(cur['month'])     # the agent can start while the other stages are still running
    return snap

@app.post('/api/jobs')
def add_jobs(body: dict = Body(...)):
    """jobs: as Jobs.add takes them; a run (of a month or a 专题) also says agent: true when the stages ticked in the
    settings go to the external agent this time, else they are asked of their models. Such a run that has to wait for
    another job first gets an estimate beside it that writes the agent's first tasks now (Jobs.LIGHT)."""
    items = body.get('jobs') or []
    handed = config.load_settings().get('external_stages') or []
    for j in items:
        if j.get('kind') in ('run', 'topic-run', 'topic-screen'):
            agent = bool(j.pop('agent', False))
            if agent and not handed:
                fail(409, '设置里还没有勾选交给外部 Agent 的环节（设置 › 接口与模型 › 外部 Agent）')
            j['api_only'] = not agent
        if j.get('kind') in TOPIC_KINDS:
            spec, state = _topic_state(_topic_dir(j.get('topic')), _topic_spec(j.get('topic')))
            todo = state['blockers']
            if todo:
                fail(409, '专题配置还不完整：' + '；'.join(todo))
            if j['kind'] != 'topic-plan':
                missing = topic.missing_stages(config.load_models(), config.load_settings(), spec)
                if missing:
                    fail(409, '这些环节还没有选择模型：' + '、'.join(missing))
            j.update(month=f'专题/{j["topic"]}', title=spec['title'])
            continue
        if j.get('kind') in ('run', 'plan') and not re.match(r'^\d{4}-\d{2}$', j.get('month') or ''):
            fail(400, '月份格式应为 YYYY-MM')
        if j.get('kind') == 'run':
            missing = config.missing_stages(config.load_models(), config.load_settings())
            if missing:
                fail(409, '这些环节还没有选择模型：' + '、'.join(s['label'] for s in missing))
        if j.get('budget') is not None and (not isinstance(j['budget'], int) or j['budget'] < 0):
            fail(400, '预算要么不填，要么是正整数（0 表示这次不限）')
    first = []
    if jobs.busy():
        snap = jobs.snapshot()
        running = {(snap[lane] or {}).get('month') for lane in LANES}
        for j in items:
            if j.get('kind') in ('run', 'topic-run', 'topic-screen') and not j['api_only'] and j['month'] not in running:
                first.append({'kind': 'plan', 'month': j['month'], 'hand_out': True} if j['kind'] == 'run' else
                             {'kind': 'topic-plan', 'topic': j['topic'], 'month': j['month'], 'title': j['title'], 'hand_out': True})
    return {'added': jobs.add(first + items)}

@app.post('/api/jobs/stop')
def stop_jobs(body: dict = Body(default={})):
    jobs.stop(bool(body.get('clear_queue')), body.get('id'))
    return jobs.snapshot()

@app.post('/api/jobs/{job_id}/move')
def move_job(job_id: str, body: dict = Body(...)):
    jobs.move(job_id, int(body.get('delta', 0)))
    return jobs.snapshot()

@app.delete('/api/jobs/{job_id}')
def remove_job(job_id: str):
    jobs.remove(job_id)
    return jobs.snapshot()

@app.get('/api/jobs/log')
def job_log(path: str, lines: int = 400):
    p = safe_path(path)
    if not p.exists():
        return {'text': ''}
    text = p.read_text(encoding='utf-8', errors='replace').splitlines()
    return {'text': '\n'.join(text[-lines:])}


# ---------- reports ----------
@app.get('/api/reports')
def reports():
    root = Path(config.load_settings()['output_root'])
    out = []
    for f in sorted(root.glob('*/月报_*.md'), reverse=True):
        if re.match(r'^\d{4}-\d{2}$', f.parent.name) and f.name == f'月报_{f.parent.name}.md':
            st = read_json(f.parent / 'status.json') or {}
            out.append({'month': f.parent.name, 'path': str(f), 'state': st.get('state'), 'tokens': st.get('tokens_used'),
                        'modified': f.stat().st_mtime})
    return {'reports': out}

def _checks_path(month):
    return config.HOME / '勾选' / f'{month}.json'

@app.get('/api/reports/{month}')
def report(month: str):
    f = Path(config.load_settings()['output_root']) / month / f'月报_{month}.md'
    if not f.exists():
        fail(404, f'{month} 还没有月报')
    return {'month': month, 'path': str(f), 'markdown': f.read_text(encoding='utf-8'),
            'checks': read_json(_checks_path(month)) or {}, 'status': read_json(f.parent / 'status.json')}

@app.put('/api/reports/{month}/checks')
def save_checks(month: str, body: dict = Body(...)):
    path = _checks_path(month)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**(read_json(path) or {}), **{k: bool(v) for k, v in body.items()}}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)
    return data

# ---------- 专题 ----------
def _topic_dir(topic_id):
    if not topic.valid_id(topic_id):
        fail(400, '专题编号不对')
    d = topic.root_dir(config.load_settings()) / topic_id
    if not (d / 'spec.json').exists():
        fail(404, '没有这个专题')
    return d

def _topic_spec(topic_id):
    return topic.load(_topic_dir(topic_id))

def _active_chats(a, spec):
    """Chats with messages in the 专题's time, so its count is what a run would read; None before a time is chosen."""
    if not (spec['range']['from'] and spec['range']['to']):
        return None
    key = (*topic.span(spec), a.latest())
    hit = _cache.get('active')
    if not hit or hit[0] != key:
        hit = _cache['active'] = (key, {c for c, _ in a.active_chats(*key[:2])})
    return hit[1]

def _topic_state(d, spec, refresh=False):
    """What still blocks the 专题 and a short account of what it reads, worked out against the open archive.
    A scope still kept as named lists is turned into a selection here and saved."""
    a, monthly = archive(), selection()
    with _alock:
        if refresh:
            a.refresh()
        spec = topic.migrate(d, spec, a, monthly)
        sc = topic.Scope(spec, monthly)
        reach = sc.reach(a, _active_chats(a, spec))
        text = sc.describe(a)
    return spec, {'blockers': topic.blockers(spec, reach), 'scope': {**reach, 'text': text}}

def _topic_job(topic_id):
    snap, month = jobs.snapshot(), f'专题/{topic_id}'
    for lane in LANES:
        if (snap[lane] or {}).get('month') == month:
            return snap[lane]['kind']
    if any(j.get('month') == month for j in snap['queue']):
        return 'queued'
    return 'locked' if lock_holder(config.load_settings()['output_root'], month) else None

@app.get('/api/topics')
def topics():
    base = topic.root_dir(config.load_settings())
    for d in base.glob('*/status.json') if base.exists() else []:
        _settle(d.parent, f'专题/{d.parent.name}')
    out = topic.listing(config.load_settings())
    for t in out:
        t['job'] = _topic_job(t['id'])
    return {'topics': out}

@app.get('/api/topics/meta')
def topic_meta():
    a = archive()
    with _alock:
        a.refresh()
        cutoff = a.latest()
        me = a.person(a.self_id)
        cats = a.categories()
        counts = Counter(x for c in a.folders for x in ancestors(a.category(c)))
    return {'perspectives': topic.PERSPECTIVES, 'templates': topic.TEMPLATES, 'details': topic.DETAILS, 'modes': topic.MODES,
            'categories': cats, 'category_chats': {c: counts.get(c, 0) for c in cats}, 'last_day': stamp(cutoff - 1).strftime('%Y-%m-%d'), 'self_name': me}

@app.get('/api/topics/people')
def topic_people(q: str = ''):
    """Friends (private chats) and anyone seen in a group whose name contains q."""
    a, q = archive(), q.strip()
    if not q:
        return {'people': []}
    low = q.lower()
    friends = [c for c in a.folders if not c.endswith('@chatroom') and c != 'filehelper' and low in (a.names.get(c) or '').lower()][:30]
    with _alock:
        rows = a.find_people(q, 40)
    out = [{'id': c, 'name': a.names.get(c, c), 'category': a.category(c)} for c in friends]
    out += [{'id': r[0], 'name': r[1] or r[0], 'category': '群成员'} for r in rows if r[0] not in set(friends)]
    return {'people': out[:50]}

@app.post('/api/topics')
def new_topic(body: dict = Body(default={})):
    tid = topic.create(config.load_settings(), topic.clean(body.get('spec') or {}))
    return topic_detail(tid)

@app.get('/api/topics/{topic_id}')
def topic_detail(topic_id: str):
    d = _topic_dir(topic_id)
    _settle(d, f'专题/{topic_id}')
    spec, state = _topic_state(d, topic.load(d), refresh=True)
    st = read_json(d / 'status.json')
    report = None
    if st and st.get('report') and Path(st['report']).exists():
        text = Path(st['text']) if st.get('text') else None
        report = {'path': st['report'], 'markdown': Path(st['report']).read_text(encoding='utf-8'),
                  'text': text.read_text(encoding='utf-8') if text and text.exists() else None, 'made': st.get('report_made'),
                  'search': st.get('report_search')}
    s = config.load_settings()
    # search: compared with the preview's and the result's own, to tell when the spec has changed since.
    return {'id': topic_id, 'folder': str(d), 'spec': spec, 'search': topic.search_digest(spec), **state,
            'status': st, 'plan': read_json(d / 'plan.json'), 'report': report,
            'progress': read_json(d / 'progress.json'), 'plan_progress': read_json(d / 'plan-progress.json'),
            'handoff': _handoff(f'专题/{topic_id}'),
            'masks': (d / 'masks.json').exists(), 'job': _topic_job(topic_id),
            'missing': topic.missing_stages(config.load_models(), s, spec)}

@app.put('/api/topics/{topic_id}')
def save_topic(topic_id: str, body: dict = Body(...)):
    """The 专题 page's fields. Its chat selection and people block are edited on the 读取范围 page, so those stay as saved."""
    d = _topic_dir(topic_id)
    new, kept = topic.clean(body.get('spec') or {}), topic.load(d)['scope']
    new['scope'] = {**kept, 'follow_monthly': new['scope']['follow_monthly']} if not kept.get('legacy') else kept
    spec, state = _topic_state(d, topic.save(d, new))
    return {'spec': spec, 'search': topic.search_digest(spec), **state,
            'missing': topic.missing_stages(config.load_models(), config.load_settings(), spec)}

@app.put('/api/topics/{topic_id}/scope')
def save_topic_scope(topic_id: str, body: dict = Body(...)):
    """From the 读取范围 page: this 专题's own selection and people block, and whether it follows the monthly selection."""
    d = _topic_dir(topic_id)
    spec, _ = _topic_state(d, topic.load(d))
    sc = dict(spec['scope'])
    for k in ('selection', 'people', 'follow_monthly'):
        if k in body:
            sc[k] = body[k]
    spec = topic.save(d, {**spec, 'scope': sc})
    spec, state = _topic_state(d, spec)
    return {'spec': spec, 'search': topic.search_digest(spec), **state}

@app.post('/api/topics/{topic_id}/scope')
def pick_topic_scope(topic_id: str, body: dict = Body(...)):
    """Add one chat or person (a name the draft could not settle) to this 专题's own selection."""
    d = _topic_dir(topic_id)
    c = body.get('pick') or {}
    if not c.get('id'):
        fail(400, '没有要添加的会话或人')
    a = archive()
    spec, _ = _topic_state(d, topic.load(d))
    sc = spec['scope']
    with _alock:
        sel = sc['selection'] or {'skip_categories': sorted({k.split('/')[0] for k in a.categories()}), 'exclude': {}, 'include': {}}
        known = c['id'] in a.folders
    if known:
        sel = {**sel, 'include': {**sel['include'], c['id']: str(c.get('name') or c['id'])},
               'exclude': {k: v for k, v in sel['exclude'].items() if k != c['id']}}
    else:
        # Someone seen only in groups: what they said there.
        sc['people'] = {**sc['people'], 'on': True, 'add': sc['people']['add'] + [{'id': c['id'], 'name': c.get('name') or c['id']}]}
    sc.update(selection=sel, follow_monthly=False)
    spec, state = _topic_state(d, topic.save(d, spec))
    return {'spec': spec, 'search': topic.search_digest(spec), **state}

@app.post('/api/topics/{topic_id}/draft')
def draft_topic(topic_id: str, body: dict = Body(...)):
    """One sentence to a spec: a model reads the request, names are matched to chats here."""
    d = _topic_dir(topic_id)
    request = str(body.get('request') or '').strip()
    if not request:
        fail(400, '先写下想总结什么')
    try:
        spec, problems = topic.draft(ROOT, archive(), config.load_settings(), config.load_models(), topic_id, request, lock=_alock, monthly=selection())
    except RuntimeError as e:
        fail(502, f'模型调用失败：{str(e)[:300]}')
    spec, state = _topic_state(d, topic.save(d, spec))
    return {'spec': spec, 'problems': problems, **state}

@app.post('/api/topics/{topic_id}/keywords')
def expand_topic_keywords(topic_id: str, body: dict = Body(...)):
    _topic_dir(topic_id)
    try:
        words = topic.expand_keywords(ROOT, config.load_settings(), config.load_models(), topic_id,
                                      str(body.get('description') or ''), [str(k) for k in body.get('keywords') or []])
    except RuntimeError as e:
        fail(502, f'模型调用失败：{str(e)[:300]}')
    return {'keywords': words}

@app.get('/api/topics/{topic_id}/matches')
def topic_matches(topic_id: str, chat: str, offset: int = 0, limit: int = 30):
    """The passages the search found in one chat, read from the archive unmasked: this is for me, not for a model."""
    entry = (read_json(_topic_dir(topic_id) / 'matches.json') or {}).get(chat)
    if not entry:
        return {'windows': [], 'total': 0}
    a = archive()
    out = []
    with _alock:
        for w in entry['windows'][offset:offset + limit]:
            lines = []
            for l in w:
                r = a.message(l['doc'], l['anchor']) if l.get('anchor') else None
                if r:
                    lines.append({'lid': l['lid'], 'time': stamp(l['ts']).strftime('%Y-%m-%d %H:%M'), 'sender': '你' if l['self'] else a.person(r['sender']),
                                  'self': l['self'], 'type': r['type'], 'body': a.readable_body(r['content'] or '', r['type'], r['sender'])[:1500],
                                  'hit': l['hit'], 'why': l.get('why') or ''})
            out.append(lines)
    return {'name': entry['name'], 'windows': out, 'total': len(entry['windows'])}

@app.get('/api/topics/{topic_id}/masks')
def topic_masks(topic_id: str):
    return _masks(_topic_dir(topic_id) / 'masks.json')

@app.delete('/api/topics/{topic_id}')
def delete_topic(topic_id: str):
    d = _topic_dir(topic_id)
    if _topic_job(topic_id):
        fail(409, '这个专题有任务正在运行或排队。先停止任务或把它移出队列，再删除。')
    try:
        recycle([d])
    except OSError as e:
        fail(500, str(e))
    return {'ok': True}


def _media_rows(row):
    a = archive()
    out = []
    for f in row.get('media') or []:
        item = {'id': media_id(f['path']), 'kind': f['kind'], 'name': Path(f['path']).name, 'status': 'ready',
                'duration': None, 'width': None, 'height': None}
        if f['kind'] == 'voice':
            item['transcript'] = read_transcript(a.path(f['path']))
        out.append(item)
    return out

def _message(a, row, lookup):
    body = a.readable_body(row['content'] or '', row['type'], row['sender'])
    is_self = row['sender'] == a.self_id
    return {'lookup_id': lookup, 'ts': row['timestamp'], 'time': stamp(row['timestamp']).strftime('%Y-%m-%d %H:%M:%S'),
            'sender': '你' if is_self else a.person(row['sender']), 'self': is_self, 'type': row['type'], 'body': body[:6000],
            'media': _media_rows(row) if row['type'] in ('图片', '视频', '语音', '文件', '动画表情') else []}

def _messages(a, rows, keep=None):
    numbers = a.ids([(m['doc'], m['anchor']) for m in rows])
    return [_message(a, m, numbers.get(m['anchor'])) for m in rows if m['type'] != '系统消息' or m['anchor'] == keep]

@app.get('/api/context/{lookup_id}')
def context(lookup_id: int, n: int = 15):
    """The message behind a report's #number with n messages before and after it, read straight from the archive."""
    a = archive()
    with _alock:
        hit = a.ref(lookup_id)
        if not hit:
            fail(404, f'归档中没有编号 #{lookup_id}')
        doc, anchor = hit
        shown, k = a.around(doc, anchor, n)
        if k is None:
            fail(404, '归档中找不到这条消息')
        chat = shown[k]['chat']
        messages = _messages(a, shown, anchor)
    return {'lookup_id': lookup_id, 'chat': chat, 'name': a.names.get(chat, chat), 'category': a.category(chat),
            'group': a.is_group(chat), 'anchor': anchor, 'messages': messages, 'target': lookup_id}

def _document(lookup_id):
    a = archive()
    with _alock:
        hit = a.ref(lookup_id)
    if not hit:
        fail(404, f'归档中没有编号 #{lookup_id}')
    return a.path(hit[0]), hit[1]

def _attachment(mid):
    """An attachment's file from its id; only files inside the archive folder."""
    a = archive()
    rel = media_path(mid)
    path = a.path(rel) if rel else None
    try:
        inside = path is not None and path.resolve().is_relative_to(a.root.resolve())
    except OSError:
        inside = False
    if not inside or not path.is_file():
        fail(404, '媒体文件不在归档中')
    return rel, path

@app.post('/api/open')
def open_path(body: dict = Body(...)):
    """Open a file with its program, show it in Explorer, or open an archive message's Markdown at its anchor."""
    if body.get('media_id'):
        path = _attachment(body['media_id'])[1]
    elif body.get('lookup_id'):
        path, anchor = _document(int(body['lookup_id']))
        viewer = config.load_settings().get('markdown_viewer')
        if viewer and not body.get('reveal'):
            subprocess.Popen([viewer, f'{path}#{anchor}'], creationflags=subprocess.CREATE_NO_WINDOW)
            return {'ok': True}
    else:
        path = safe_path(body.get('path') or '')
    if not path.exists():
        fail(404, f'文件不存在：{path}')
    viewer = config.load_settings().get('markdown_viewer')
    if body.get('reveal'):
        subprocess.Popen(['explorer.exe', '/select,', str(path)])
    elif viewer and path.suffix.lower() in ('.md', '.markdown'):
        subprocess.Popen([viewer, str(path)], creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.startfile(str(path))
    return {'ok': True}

@app.post('/api/open_url')
def open_url(body: dict = Body(...)):
    import webbrowser
    url = str(body.get('url') or '')
    if not re.match(r'^https?://', url, re.I):
        fail(400, '只能打开 http 和 https 链接')
    webbrowser.open(url)
    return {'ok': True}

@app.get('/api/media/{media_id}')
def media(media_id: str, thumb: int = 0):
    rel, path = _attachment(media_id)
    kind = media_kind(rel)
    if kind == 'image' and (thumb or path.suffix.lower() not in BROWSER_IMAGES):
        try:
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert('RGB')
                im.thumbnail((480, 480) if thumb else (2400, 2400))
                buf = io.BytesIO()
                im.save(buf, 'JPEG', quality=82)
            return Response(buf.getvalue(), media_type='image/jpeg', headers={'Cache-Control': 'max-age=86400'})
        except Exception:
            if kind == 'image':
                fail(415, f'无法预览这种格式（{path.suffix}）')
    return FileResponse(path, headers={'Cache-Control': 'max-age=86400'})


# ---------- chats and selection ----------
def _pinyin(name):
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return '', ''
    full = lazy_pinyin(name, errors='ignore')
    return ''.join(full).lower(), ''.join(lazy_pinyin(name, style=Style.FIRST_LETTER, errors='ignore')).lower()

def chat_stats():
    """Per chat, per month counts over the last 12 complete months, cached by archive cutoff."""
    a = archive()
    cutoff = a.latest()
    path = config.HOME / 'cache' / 'chat_stats.json'
    cached = _cache.get('chat_stats') or read_json(path)
    if cached and cached.get('cutoff') == cutoff and cached.get('v') == 2:
        _cache['chat_stats'] = cached
        return cached
    last = stamp(cutoff).strftime('%Y-%m')
    first = month_list((datetime.strptime(last, '%Y-%m') - timedelta(days=330)).strftime('%Y-%m'), last)
    start = month_bounds(first[0])[0]
    months = month_list(first[0], last)
    stats = {}
    with _alock:
        for chat in a.folders:
            by, last = a.month_counts(chat, start)
            stats[chat] = {'m': [by.get(mo, (0, 0))[0] for mo in months], 'mine': sum(v[1] for v in by.values()), 'last': last}
    data = {'v': 2, 'cutoff': cutoff, 'months': months, 'stats': stats}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    _cache['chat_stats'] = data
    return data

@app.get('/api/chats')
def chats(topic_id: str = Query('', alias='topic')):
    a = archive()
    with _alock:
        a.refresh()
        cats = a.categories()
    data = chat_stats()
    sel = selection()
    rows = []
    for chat, folder in a.folders.items():
        name = a.names.get(chat) or chat
        st = data['stats'].get(chat, {'m': [], 'mine': 0, 'last': None})
        full, initials = _cache.setdefault('py', {}).get(name) or _cache['py'].setdefault(name, _pinyin(name))
        cat = a.category(chat)
        rows.append({'chat': chat, 'name': name, 'category': cat, 'folder': folder, 'group': chat.endswith('@chatroom'),
                     'monthly': st['m'], 'messages': sum(st['m']), 'mine': st['mine'] if chat != 'filehelper' else sum(st['m']),
                     'last': st['last'], 'locked': chat in a.excluded, 'py': full, 'pyi': initials})
    out = {'months': data['months'], 'chats': rows, 'selection': sel.data, 'categories': cats}
    if topic_id:
        # A 专题's 读取范围: its own selection (None before anything was chosen), or the monthly one when it follows that.
        d = _topic_dir(topic_id)
        spec, state = _topic_state(d, topic.load(d))
        sc = spec['scope']
        out['topic'] = {'id': topic_id, 'title': spec['title'], 'follow_monthly': sc['follow_monthly'], 'people': sc['people'], **state}
        if not sc['follow_monthly']:
            out['selection'] = sc['selection']
    return out

@app.get('/api/chats/{chat}/preview')
def chat_preview(chat: str):
    a = archive()
    if chat not in a.folders:
        fail(404, '没有这个会话')
    with _alock:
        recent = _messages(a, a.recent(chat, 20))
    root = Path(config.load_settings()['output_root'])
    mentions = []
    for d in sorted(root.glob('[0-9][0-9][0-9][0-9]-[0-9][0-9]'), reverse=True)[:3]:
        for it in read_json(d / 'at_me.json') or []:
            if it.get('chat') == chat:
                mentions.append({'month': d.name, 'kind': '@我', 'text': f'{it.get("who", "")}：{it.get("what", "")}', 'lookup_id': it.get('lookup_id')})
        cards = d / 'cards.jsonl'
        if cards.exists():
            for line in cards.read_text(encoding='utf-8').splitlines():
                c = json.loads(line)
                if c.get('chat') == chat and c.get('open_loop') and c.get('sensitivity') != '仅定位':
                    mentions.append({'month': d.name, 'kind': '未办完', 'text': c.get('title') or c.get('summary', '')[:40]})
    return {'recent': recent, 'mentions': mentions[:20]}

@app.put('/api/selection')
def save_selection(body: dict = Body(...)):
    sel = selection()
    sel.skip_categories = list(body.get('skip_categories') or [])
    sel.exclude = dict(body.get('exclude') or {})
    sel.include = dict(body.get('include') or {})
    overlap = set(sel.exclude) & set(sel.include)
    if overlap:
        fail(400, '同一个会话不能既手动排除又手动保留')
    sel.save()
    return sel.data

@app.post('/api/selection/check')
def check_selection(body: dict = Body(...)):
    """Chats about to become skipped that had @我 or open loops in the last three reports."""
    chats_ = body.get('chats') or []
    found = []
    for chat in chats_[:500]:
        hits = chat_preview(chat)['mentions'] if chat in archive().folders else []
        if hits:
            found.append({'chat': chat, 'name': archive().names.get(chat, chat), 'items': hits[:5]})
    return {'warnings': found}


# ---------- voice ----------
@app.get('/api/voice')
def voice_list(first: str, last: str, chat: str = '', offset: int = 0, limit: int = 200, state: str = ''):
    a = archive()
    s = config.load_settings()
    start, end = month_bounds(first)[0], month_bounds(last)[1]
    with _alock:
        rows = voice.clips(a, selection(), start, end, [chat] if chat else None)
    items = []
    for r in rows:
        text = read_transcript(a.path(r['main_path']))
        items.append({'id': r['id'], 'chat': r['chat'], 'name': a.names.get(r['chat'], r['chat']), 'ts': r['timestamp'],
                      'time': stamp(r['timestamp']).strftime('%Y-%m-%d %H:%M'), 'sender': '你' if r['sender'] == a.self_id else a.person(r['sender']),
                      'text': text})
    done = [i for i in items if i['text'] is not None]
    pace = voice_pace(s['output_root']) or 0.6
    view = [i for i in items if not state or (state == 'done') == (i['text'] is not None)]
    view.sort(key=lambda i: -i['ts'])
    return {'total': len(items), 'done': len(done), 'pending': len(items) - len(done), 'pace': pace,
            'eta_seconds': int((len(items) - len(done)) * pace), 'items': view[offset:offset + limit], 'matched': len(view)}

@app.get('/api/voice/progress')
def voice_progress():
    return {'progress': read_json(Path(config.load_settings()['output_root']) / '语音转写' / 'progress.json')}

@app.get('/api/voice/service')
def voice_service():
    m = config.load_models()
    name = m['stages'].get('asr')
    if not name:
        return {'ok': False, 'state': 'unset', 'message': '"语音转写"环节还没有选择模型'}
    spec = m['models'][name]
    p = m['providers'][spec['provider']]
    return {**probe_provider(spec['provider'], p, timeout=3), 'model': spec['model'], 'base_url': p['base_url']}


# ---------- usage ----------
def _price_for(m, row):
    spec = m['models'].get(row.get('model_id') or '') or next((v for v in m['models'].values() if v['model'] == row.get('model') and v.get('price')), None)
    return (spec or {}).get('price') or {}

@app.get('/api/usage')
def usage():
    """The calls behind each month's report and each 专题 as they are now, compact, with an estimated cost from the
    current prices. A finished run leaves only the calls its result uses (Models.keep_current), and a deleted
    result is not listed."""
    s, m = config.load_settings(), config.load_models()
    rows, titles, states, hidden = [], {}, {}, {}
    since, shown = s.get('usage_since') or 0, set(s.get('usage_shown') or [])
    out_root = Path(s['output_root'])
    for f in list(out_root.glob('[0-9][0-9][0-9][0-9]-[0-9][0-9]/receipts.jsonl')) + list(topic.root_dir(s).glob('*/receipts.jsonl')):
        if f.parent.parent == topic.root_dir(s):
            month = f'专题/{f.parent.name}'
            try:
                titles[f.parent.name] = topic.load(f.parent)['title']
            except (OSError, ValueError, config.ConfigError):
                continue      # not a 专题 any more
        else:
            month = f.parent.name
            if not (f.parent / 'status.json').exists():
                continue      # deleted with its cache kept: the records wait for the month to be generated again
        if not lock_holder(s['output_root'], month):
            try:
                receipts.settle(f.parent)       # records from before a finished run reduced them itself
            except OSError as e:
                print(f'整理 {month} 的用量记录时出错：{e}', file=sys.stderr)
        states[month] = (read_json(f.parent / 'status.json') or {}).get('state')
        for line in f.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                ts = datetime.fromisoformat(r['at']).replace(tzinfo=TZ).timestamp()
            except (ValueError, KeyError):
                continue
            ok = r.get('status') == 'ok'
            pin, pout, cached = r.get('prompt_tokens', 0), r.get('completion_tokens', 0), r.get('cached_tokens', 0)
            if ts < since and month not in shown:
                h = hidden.setdefault(month, {'owner': month, 'calls': 0, 'tokens': 0})
                h['calls'] += 1
                h['tokens'] += pin + pout if ok else 0
                continue
            price = _price_for(m, r)
            cost = None
            if r.get('api') == 'external':            # an external agent's answer: nothing was paid through an interface
                price, cost = {'currency': 'local'}, 0.0
            elif price.get('local'):
                price, cost = {'currency': 'local'}, 0.0
            elif ok and price.get('input') is not None and price.get('output') is not None:
                ci = price.get('cached_input', price['input'])
                cost = ((pin - cached) * price['input'] + cached * ci + pout * price['output']) / 1e6
            rows.append([int(ts), month, r['stage'], r.get('model_id') or r.get('model'), r.get('model'), 1 if ok else 0,
                         pin, pout, cached, r.get('reasoning_tokens', 0), r.get('seconds', 0), cost, price.get('currency')])
    rows.sort(key=lambda r: r[0])
    stage_labels = {**{st['key']: st['label'] for st in config.STAGES}, **topic.STAGE_LABELS}
    return {'columns': ['ts', 'month', 'stage', 'model_id', 'model', 'ok', 'in', 'out', 'cached', 'reasoning', 'seconds', 'cost', 'currency'],
            'rows': rows, 'stage_labels': stage_labels, 'topics': titles, 'states': states,
            'since': since or None, 'hidden': sorted(hidden.values(), key=lambda h: h['owner'], reverse=True)}


# ---------- settings ----------
@app.get('/api/settings')
def get_settings():
    s = config.load_settings()
    return {'settings': s, 'defaults': config.DEFAULTS, 'home': str(config.HOME),
            'external': {'stages': [{'key': st['key'], 'label': st['label']} for st in config.STAGES if st['key'] in config.EXTERNAL_STAGES],
                         'default_root': str(external.root({**s, 'external_root': ''})),
                         # the saved folder, and whether a task has been written there yet (only then is there anything to open)
                         'root': str(external.root(s)), 'root_exists': external.root(s).is_dir()}}

@app.put('/api/settings')
def put_settings(body: dict = Body(...)):
    if body.get('archive_root') and not Path(body['archive_root']).is_dir():
        fail(400, f'归档目录不存在：{body["archive_root"]}')
    if 'output_root' in body:
        try:
            Path(body['output_root']).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            fail(400, f'无法使用输出目录：{e}')
    if body.get('markdown_viewer') and not Path(body['markdown_viewer']).exists():
        fail(400, '找不到这个 Markdown 查看器程序')
    if body.get('external_root'):
        try:
            Path(body['external_root']).mkdir(parents=True, exist_ok=True)
        except (OSError, TypeError) as e:
            fail(400, f'无法使用交接文件夹：{e}')
    return {'settings': config.save_settings(body)}

@app.post('/api/settings/reset')
def reset_settings(body: dict = Body(...)):
    keys = body.get('keys') or []
    path = config.settings_path()
    stored = read_json(path) or {}
    for k in keys:
        stored.pop(k, None)
    config._write(path, stored)
    return {'settings': config.load_settings()}

def _key_state(name, p):
    t = (p.get('key') or {}).get('type', 'none')
    if t == 'credential':
        return 'saved' if config.credential_get(name) else 'missing'
    return t

@app.get('/api/models')
def get_models():
    m, s = config.load_models(), config.load_settings()
    fits = {st['key']: {name: config.fit_reason(m, st['key'], name) for name in m['models']} for st in config.STAGES}
    providers = {name: {**p, 'key_state': _key_state(name, p)} for name, p in m['providers'].items()}
    # order: the user's order of the models, which a JS object would not keep for names that look like numbers.
    return {'providers': providers, 'models': m['models'], 'order': list(m['models']), 'stages': m['stages'], 'stage_defs': config.STAGES,
            'apis': config.APIS, 'fits': fits, 'missing': [x['key'] for x in config.missing_stages(m, s)],
            'switches': {st['switch']: s.get(st['switch']) for st in config.STAGES if st.get('switch')},
            'fallback': m['fallback'], 'fallback_labels': config.FALLBACK_LABELS}

@app.post('/api/providers')
def save_provider(body: dict = Body(...)):
    config.save_provider(body.get('name'), body.get('data') or {}, body.get('old_name'))
    if body.get('secret'):
        config.credential_set(body['name'], body['secret'])
    return get_models()

@app.delete('/api/providers/{name}')
def delete_provider(name: str):
    config.delete_provider(name)
    return get_models()

@app.put('/api/providers/{name}/key')
def set_key(name: str, body: dict = Body(...)):
    if name not in config.load_models()['providers']:
        fail(404, '没有这个接口')
    if body.get('secret'):
        config.credential_set(name, body['secret'])
    else:
        config.credential_delete(name)
    return get_models()

def probe_provider(name, p, timeout=15):
    """Reach the provider and list its models; says what went wrong in words a user can act on."""
    import openai
    started = time.time()
    try:
        key = config.resolve_key(name, p)
    except config.ConfigError as e:
        return {'ok': False, 'state': 'key', 'message': str(e)}
    try:
        client = openai.OpenAI(base_url=p['base_url'], api_key=key, timeout=timeout, max_retries=0)
        ids = sorted(x.id for x in client.models.list())
        return {'ok': True, 'state': 'ok', 'models': ids, 'ms': int((time.time() - started) * 1000)}
    except openai.AuthenticationError:
        return {'ok': False, 'state': 'auth', 'message': '密钥无效或已过期'}
    except openai.APIConnectionError:
        hint = '请确认这台电脑上的服务已经启动' if 'localhost' in p['base_url'] or '127.0.0.1' in p['base_url'] else '请检查地址和网络'
        return {'ok': False, 'state': 'down', 'message': f'连不上 {p["base_url"]}：{hint}'}
    except openai.APIStatusError as e:
        return {'ok': False, 'state': 'error', 'message': f'接口返回 {e.status_code}：{str(e)[:200]}'}
    except Exception as e:
        return {'ok': False, 'state': 'error', 'message': f'{type(e).__name__}: {str(e)[:200]}'}

@app.post('/api/providers/probe')
def probe(body: dict = Body(...)):
    """Test a saved provider (name) or one being edited (data, with an optional secret not yet saved)."""
    name = body.get('name') or ''
    m = config.load_models()
    if body.get('data'):
        p = config._clean_provider(body['data'])
        if body.get('secret'):
            p = {**p, 'key': {'type': 'inline'}}
            import openai
            try:
                client = openai.OpenAI(base_url=p['base_url'], api_key=body['secret'], timeout=15, max_retries=0)
                return {'ok': True, 'state': 'ok', 'models': sorted(x.id for x in client.models.list())}
            except Exception as e:
                return {'ok': False, 'state': 'error', 'message': f'{type(e).__name__}: {str(e)[:200]}'}
    elif name in m['providers']:
        p = m['providers'][name]
    else:
        fail(404, '没有这个接口')
    return probe_provider(name, p)

@app.post('/api/models')
def save_model(body: dict = Body(...)):
    config.save_model(body.get('name'), body.get('data') or {}, body.get('old_name'))
    return get_models()

@app.post('/api/models/{name}/move')
def move_model(name: str, body: dict = Body(...)):
    by = body.get('by')
    if by not in (-1, 1):
        fail(400, 'by 只能是 -1 或 1')
    config.move_model(name, by)
    return get_models()

@app.delete('/api/models/{name}')
def delete_model(name: str):
    config.delete_model(name)
    return get_models()

@app.post('/api/models/{name}/test')
def test_model(name: str):
    """One very small real call, to show the model answers with these parameters."""
    import openai
    from .llm import Models
    m = config.load_models()
    if name not in m['models']:
        fail(404, '没有这个模型')
    spec = m['models'][name]
    p = m['providers'][spec['provider']]
    if p['api'] == 'audio':
        return probe_provider(spec['provider'], p)
    started = time.time()
    try:
        client = openai.OpenAI(base_url=p['base_url'], api_key=config.resolve_key(spec['provider'], p), timeout=120, max_retries=0)
        call = Models._responses if p['api'] == 'responses' else Models._chat
        text, u, finish = call(client, spec, '只回复一个 JSON 对象。', '请回复 {"ok": true}', True)
        return {'ok': finish == 'stop', 'state': 'ok', 'answer': text[:200], 'usage': u, 'ms': int((time.time() - started) * 1000)}
    except config.ConfigError as e:
        return {'ok': False, 'state': 'key', 'message': str(e)}
    except openai.APIError as e:
        return {'ok': False, 'state': 'error', 'message': f'{type(e).__name__}: {str(e)[:300]}'}

@app.put('/api/stages')
def put_stages(body: dict = Body(...)):
    for stage, name in body.get('stages', {}).items():
        config.set_stage(stage, name)
    if body.get('switches'):
        allowed = {st['switch'] for st in config.STAGES if st.get('switch')}
        config.save_settings({k: bool(v) for k, v in body['switches'].items() if k in allowed})
    if 'fallback' in body:
        fb = body['fallback'] or {}
        config.set_fallback(fb.get('enabled'), fb.get('models'))
    return get_models()


# ---------- diagnostics ----------
# ---------- configuration bundle ----------
@app.get('/api/config')
def config_info():
    home = config.HOME
    files = [{'name': n, 'exists': (home / n).exists()} for n in config.CONFIG_FILES]
    ticks = len(list((home / config.TICKS).glob('*.json'))) if (home / config.TICKS).is_dir() else 0
    return {'home': str(home), 'files': files, 'ticks': ticks, 'export_path': config.default_export_path(), 'import_path': config.latest_export()}

@app.post('/api/config/export')
def config_export(body: dict = Body(...)):
    return config.export_config(str(body.get('path') or '').strip().strip('"'))

@app.post('/api/config/import')
def config_import(body: dict = Body(...)):
    return config.import_config(str(body.get('path') or '').strip().strip('"'))

@app.get('/api/diagnostics')
def diagnostics():
    s = config.load_settings()
    out = {'version': __version__, 'home': str(config.HOME), 'program': str(PROGRAM), 'output_root': s['output_root'],
           'archive_root': s['archive_root'], 'python': sys.version.split()[0]}
    problem = location_problem(s['archive_root'])
    out['archive'] = {'ok': problem is None, 'path': s['archive_root'], 'problem': problem}
    if problem is None:
        try:
            a = archive()
            out['archive'].update(cutoff=stamp(a.latest() - 1).strftime('%Y-%m-%d %H:%M'), chats=len(a.folders))
        except HTTPException as e:               # still reading, or the read failed
            out['archive']['problem'] = e.detail
    m = config.load_models()
    out['providers'] = [{'name': n, 'base_url': p['base_url'], 'api': p['api'], 'key_state': _key_state(n, p)} for n, p in m['providers'].items()]
    try:
        import keyring
        out['keyring'] = type(keyring.get_keyring()).__name__
    except Exception as e:
        out['keyring'] = f'不可用：{e}'
    return out

@app.post('/api/diagnostics/providers')
def diagnostics_providers():
    m = config.load_models()
    return {n: probe_provider(n, p, timeout=8) for n, p in m['providers'].items()}


def make(on_change=None):
    global jobs
    jobs = Jobs(on_change)
    jobs.on_idle = _resume_waiting
    return app
