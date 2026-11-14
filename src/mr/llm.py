"""All model calls go through here: cache, receipts, retries, the budget stop, and stages handed to an external agent."""
import hashlib, json, secrets, threading, time
from collections import Counter
from datetime import datetime
from pathlib import Path
import openai
from . import anthropic_messages as am, external as ext
from .config import call_spec, fallbacks_for, resolve_key

class BudgetStop(Exception):
    pass

class ExternalPending(Exception):
    """A stage handed to an external agent has tasks without an accepted result; the run waits for them."""

class _Failed(Exception):
    """Every attempt with one model failed; error is the last one."""
    def __init__(self, error):
        super().__init__(str(error))
        self.error = error

def new_client(provider, key, timeout):
    """A client for the provider's 协议: Anthropic Messages, or one of OpenAI's."""
    if provider.get('api') == 'anthropic':
        return am.client(provider['base_url'], key, timeout)
    return openai.OpenAI(api_key=key, base_url=provider['base_url'], timeout=timeout, max_retries=0)

def caller(api):
    """The function that makes one call on this 协议: (client, spec, system, user, json_mode, images) -> (text, usage, finish)."""
    return {'responses': Models._responses, 'anthropic': am.ask}.get(api, Models._chat)

class Models:
    def __init__(self, models_config, run_dir, budget_tokens=None, systems=None, progress=None, external=None, carry_over=False):
        """external: what external.options gives (the stages handed to an agent, where each keeps its files, the percent re-asked), or None.
        carry_over: the run continues an unfinished one, so an answer it already has for a block is used even when the
        stage's model or instructions changed since; only blocks without an answer are asked, of the current model."""
        self.config = models_config
        self.run_dir = Path(run_dir)
        self.cache_dir = self.run_dir / 'cache'
        self.receipts = self.run_dir / 'receipts.jsonl'
        self.budget = budget_tokens
        self.systems = systems or {}
        self.progress = progress
        self.lock = threading.Lock()
        self.clients = {}
        self.used = self._used_so_far()
        self.calls = 0
        self.failures_in_a_row = 0
        self.external = external
        self.carry_over = carry_over
        self._earlier = {}                 # stage -> {block key: answer files kept under other models or instructions}
        self.asked = set()                # (stage, answer file name) of every answer this run used, cached or new
        self._tasks = {}                   # stage -> {block key: [names of its task files in the handoff folder]}
        self.waiting = {}                  # (stage, name) -> task file, for calls an agent has yet to answer
        self.rejected = Counter()          # stage -> results not accepted in this run
        self.external_used = Counter()     # stage -> answers in use that an agent wrote
        self.audit = Counter()             # checked, items, covered, replaced: agent answers compared with the stage's model

    def _used_so_far(self):
        if not self.receipts.exists():
            return 0
        total = 0
        for line in self.receipts.read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            total += r.get('prompt_tokens', 0) + r.get('completion_tokens', 0)
        return total

    def model_for(self, stage):
        name = self.config['stages'][stage]
        return name, self.config['models'][name]

    def _client(self, provider_name):
        if provider_name not in self.clients:
            p = self.config['providers'][provider_name]
            self.clients[provider_name] = new_client(p, resolve_key(provider_name, p), 900)
        return self.clients[provider_name]

    def _path(self, stage, key, system=None):
        # An answer depends on the prompt and on where and how it was asked, so a new result (generating a finished
        # month again) asks again when either changed. A model's name, price or note are not part of this.
        system = self.systems.get(stage, '') if system is None else system
        version = hashlib.sha256((system + json.dumps(call_spec(self.config, stage), sort_keys=True)).encode('utf-8')).hexdigest()[:8]
        return self.cache_dir / stage / f'{key}-{version}.json'

    def has_model(self, stage):
        name = self.config['stages'].get(stage)
        return bool(name and name in self.config['models'])

    def _found(self, stage, key, system=None):
        """The answer file this call uses, or None: the one asked as things are now; when continuing an unfinished
        run, otherwise the newest one kept for the same block under another model or instructions."""
        path = self._path(stage, key, system)
        if path.exists() or not self.carry_over:
            return path if path.exists() else None
        with self.lock:
            if stage not in self._earlier:
                found = self._earlier[stage] = {}
                folder = self.cache_dir / stage
                for f in folder.glob('*.json') if folder.is_dir() else []:
                    found.setdefault(f.stem.rsplit('-', 1)[0], []).append(f)
            earlier = [f for f in self._earlier[stage].get(key, []) if f.exists()]
        return max(earlier, key=lambda f: f.stat().st_mtime, default=None)

    def cached(self, stage, key, system=None):
        if not self.has_model(stage):          # an estimate before any model is chosen: nothing can have been answered
            return None
        path = self._found(stage, key, system)
        return json.loads(path.read_text(encoding='utf-8')) if path else None

    def _receipt(self, **fields):
        fields['at'] = datetime.now().isoformat(timespec='seconds')
        with self.lock, self.receipts.open('a', encoding='utf-8') as f:
            f.write(json.dumps(fields, ensure_ascii=False) + '\n')

    def _event(self, text):
        if self.progress:
            self.progress.event(text)

    def call(self, stage, key, system, user, json_mode=True, validate=None, images=None, compare=None):
        """Return the parsed answer, from cache when this exact request was already answered.

        images: optional list of data URLs sent after the text; the caller puts their hashes into key.
        compare: for a stage an agent may answer, compare(agent answer, model answer) -> (items the model found,
        how many of them the agent's answer covers); a sample of the agent's answers is checked with it.
        """
        found = self._found(stage, key, system) if self.has_model(stage) else None
        hit = json.loads(found.read_text(encoding='utf-8')) if found else None
        with self.lock:
            # The file actually used: its receipt is what this answer cost, whichever model gave it.
            self.asked.add((stage, (found or self._path(stage, key, system)).stem))
        if hit is None:
            if self.external and stage in self.external['stages'] and not images:
                hit = self._external(stage, key, system, user, json_mode, validate, compare)
            else:
                hit = self._paid(stage, key, system, user, json_mode, validate, images)
            self._keep(self._path(stage, key, system), hit)
        self._count(stage, hit)
        return hit

    @staticmethod
    def _keep(path, answer):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer, ensure_ascii=False), encoding='utf-8')

    def _count(self, stage, answer):
        if not isinstance(answer, dict):
            return
        with self.lock:
            if answer.get('_external'):
                self.external_used[stage] += 1
            a = answer.get('_audit')
            if a:
                self.audit.update(checked=1, items=a['items'], covered=a['covered'], replaced=0 if answer.get('_external') else 1)

    def _paid(self, stage, key, system, user, json_mode, validate, images):
        """The answer of the stage's model, or of a fallback when it keeps failing. Not kept here."""
        with self.lock:
            if self.budget is not None and self.used >= self.budget:
                raise BudgetStop(stage)
            if self.failures_in_a_row >= 6:
                raise RuntimeError('连续 6 次调用失败，已停止，请检查接口')
        primary = self.config['stages'][stage]
        last_error = None
        for slot, name in enumerate([primary] + [n for n in fallbacks_for(self.config, stage) if n != primary]):
            if slot:
                self._event(f'{stage}：{primary} 多次失败，改用兜底模型 {name}')
            try:
                # A fallback's answer is kept where the stage's own would be, so resuming the run does not ask again.
                answer = self._ask(stage, key, name, system, user, json_mode, validate, images, slot, self._path(stage, key, system).stem)
            except _Failed as e:
                last_error = e.error
                continue
            with self.lock:
                self.failures_in_a_row = 0
            return answer
        with self.lock:
            self.failures_in_a_row += 1
        raise RuntimeError(f'{stage} {key} 调用失败：{last_error}')

    # ---------- stages handed to an external agent ----------
    def _external(self, stage, key, system, user, json_mode, validate, compare):
        """The agent's answer when its result file is there and passes the checks an API answer passes.
        Otherwise the task file is (re)written and ExternalPending is raised; a result that fails is set aside with the reason."""
        name, task, spec, result, aside = self._handoff(stage, key, system, json_mode)
        slot = self._path(stage, key, system).stem
        receipt = dict(stage=stage, key=key, answer=slot, model_id='external', provider='external', model='外部 Agent', api='external',
                       attempt=0, seconds=0, **self._usage(0, 0))
        if result.exists():
            try:
                answer = ext.read_result(result, json_mode)
                if validate:
                    validate(answer)
            except ext.FAULTS as e:
                reason = ext.reason(e)
                aside.mkdir(parents=True, exist_ok=True)
                result.replace(aside / result.name)
                (aside / (name + '.txt')).write_text(reason + '\n', encoding='utf-8')
                self._receipt(**receipt, status='error', error=reason)
                with self.lock:
                    self.rejected[stage] += 1
            else:
                with self.lock:
                    self.waiting.pop((stage, name), None)
                answer['_external'] = True
                answer = self._audited(stage, key, system, user, json_mode, validate, compare, slot, answer)
                # Kept before the agent's file goes, so a stop in between loses nothing.
                self._keep(self._path(stage, key, system), answer)
                self._receipt(**receipt, finish='stop', status='ok')
                for f in (task, spec, result, aside / result.name, aside / (name + '.txt')):
                    f.unlink(missing_ok=True)
                return answer
        self._post(stage, key, system, user, json_mode, validate)
        raise ExternalPending(stage)

    def handoff_name(self, stage, key, system):
        """What a call's files are called in the handoff folder: the block and the instructions it is read under.
        Not the stage's model, which has no part in an agent's answer, so choosing another model orphans nothing there.
        A task already written for this block under these same instructions keeps the name it has, whatever scheme
        named it: an agent may be working from it, and its result must be found."""
        name = f'{key}-{hashlib.sha256(system.encode("utf-8")).hexdigest()[:8]}'
        folder = ext.stage_dir(self.external, stage) / ext.TASKS
        with self.lock:
            if stage not in self._tasks:
                found = self._tasks[stage] = {}
                for f in folder.glob('*.md') if folder.is_dir() else []:
                    found.setdefault(f.stem.rsplit('-', 1)[0], []).append(f.stem)
            known = list(self._tasks[stage].get(key, []))
        if name in known:
            return name
        return next((other for other in known if ext.same_instructions(folder / (other + '.md'), system)), name)

    def _handoff(self, stage, key, system, json_mode):
        system = self.systems.get(stage, '') if system is None else system
        name, base = self.handoff_name(stage, key, system), ext.stage_dir(self.external, stage)
        return (name, base / ext.TASKS / (name + '.md'), base / ext.CHECKS / (name + '.json'),
                base / ext.RESULTS / (name + ('.json' if json_mode else '.md')), base / ext.REJECTED)

    def _post(self, stage, key, system, user, json_mode, validate):
        name, task, spec, result, _ = self._handoff(stage, key, system, json_mode)
        for d in (task.parent, result.parent, spec.parent):
            d.mkdir(parents=True, exist_ok=True)
        task.write_text(ext.task_text(result, system, user), encoding='utf-8')
        spec.write_text(ext.spec_text(validate, json_mode), encoding='utf-8')
        with self.lock:
            self.waiting[(stage, name)] = task
            names = self._tasks.setdefault(stage, {}).setdefault(key, [])
            if name not in names:
                names.append(name)

    def results_in(self):
        """Whether a result file has arrived for a call that was waiting: the run can take it in instead of stopping."""
        with self.lock:
            tasks = list(self.waiting.values())
        return any((t.parent.parent / ext.RESULTS / (t.stem + suffix)).exists() for t in tasks for suffix in ('.json', '.md'))

    def offer(self, stage, key, system, user, json_mode=True, validate=None):
        """Ahead of the calls of a batch: for a stage an agent answers, write this call's task now when it has neither an
        answer nor a result to read. True when it waits for the agent; False when the call can be made."""
        if not (self.external and stage in self.external['stages']) or not self.has_model(stage) \
                or self.cached(stage, key, system) is not None:
            return False                       # a handed stage keeps its model (the audit asks it): no model yet, no task yet
        if self._handoff(stage, key, system, json_mode)[3].exists():
            return False
        self._post(stage, key, system, user, json_mode, validate)
        return True

    def clear_stale(self, stage, names):
        """Drop the handoff files of a stage that belong to none of its current calls (other instructions, blocks cut differently)."""
        if self.external and stage in self.external['stages']:
            ext.prune_stage(ext.stage_dir(self.external, stage), names)

    def _sampled(self, stage, name):
        """Whether this answer is one of those checked. The choice hangs on a value kept with the run, not in the handoff folder."""
        percent = self.external.get('audit') or 0
        if percent <= 0:
            return False
        salt = self.cache_dir / 'audit-salt'
        with self.lock:
            if not salt.exists():
                salt.parent.mkdir(parents=True, exist_ok=True)
                salt.write_text(secrets.token_hex(16), encoding='utf-8')
            seed = salt.read_text(encoding='utf-8')
        return int.from_bytes(hashlib.sha256(f'{seed}{stage}{name}'.encode('utf-8')).digest()[:4], 'big') % 100 < percent

    def _audited(self, stage, key, system, user, json_mode, validate, compare, name, answer):
        """For a sampled answer, ask the stage's model the same thing and compare. When the agent's answer misses
        something the model found, the model's answer is the one used. Returns the answer to keep."""
        if compare is None or not self._sampled(stage, name):
            return answer
        second = self.cache_dir / f'{stage}.audit' / f'{name}.json'
        try:
            model = json.loads(second.read_text(encoding='utf-8')) if second.exists() else None
            if model is None:
                model = self._paid(stage, key, system, user, json_mode, validate, None)
                self._keep(second, model)
        except BudgetStop:
            raise
        except Exception as e:
            # Whatever keeps the model from answering (the interface, a missing key), the agent's answer has passed
            # the checks and stands; only the comparison is skipped.
            self._event(f'{stage}：抽查没有做成，这一块没有抽查（{str(e)[:120]}）')
            return answer
        if model.get('_refused'):
            return answer
        items, covered = compare(answer, model)
        mark = {'items': items, 'covered': covered}
        return {**answer, '_audit': mark} if covered >= items else {**model, '_audit': mark}

    def keep_current(self, always=('topic_plan',)):
        """Leave in the receipts only what this finished result cost: the calls behind the answers it uses.
        Calls for answers a rerun no longer uses, and failed calls, go; the usage page then shows this version, not its history.
        always: stages asked outside a run, whose calls belong to the result all the same."""
        if not self.receipts.exists():
            return
        with self.lock:
            asked = set(self.asked)
        keys = {(stage, name.rsplit('-', 1)[0]) for stage, name in asked}
        rows = [json.loads(line) for line in self.receipts.read_text(encoding='utf-8').splitlines() if line.strip()]
        last = {}                   # one call per answer and kind (paid, or written by an agent): the latest that succeeded
        for i, r in enumerate(rows):
            if r.get('stage') in always or r.get('status') != 'ok':
                continue
            if r.get('answer'):
                if (r['stage'], r['answer']) in asked:
                    last[r['stage'], r['answer'], r.get('api') == 'external'] = i
            elif (r['stage'], r.get('key')) in keys:        # written before answers were named in receipts
                last[r['stage'], r['key'], None] = i
        keep = set(last.values()) | {i for i, r in enumerate(rows) if r.get('stage') in always}
        tmp = self.receipts.with_suffix('.tmp')
        tmp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for i, r in enumerate(rows) if i in keep), encoding='utf-8')
        tmp.replace(self.receipts)
        with self.lock:
            self.used = self._used_so_far()

    def external_state(self):
        """What is waiting for the agent, for the run to report; files of tasks no longer waiting are removed."""
        with self.lock:
            waiting, rejected = dict(self.waiting), dict(self.rejected)
        ext.prune(self.external, sorted({s for s, _ in waiting} | set(self.external['stages']) | set(self.config['stages'])), set(waiting))
        from .jobs import _mr                # how this program's command line is started, for the agent's own check
        names = sorted({ext.label(self.external, s) for s in self.external['stages']})
        folders = ext.owner_folders(self.external['root'], names, self.external['owner'])      # this month's or 专题's own
        return {'folder': str(self.external['root']), 'prompt': ext.prompt(folders or [self.external['root']], _mr()),
                'pending': dict(Counter(stage for stage, _ in waiting)), 'rejected': rejected}

    def _ask(self, stage, key, name, system, user, json_mode, validate, images, slot, answer_name=None):
        """Up to three attempts with one model; the parsed answer, or _Failed with the last error."""
        spec = self.config['models'][name]
        client = self._client(spec['provider'])
        api = self.config['providers'][spec['provider']].get('api', 'responses')
        base = dict(stage=stage, key=key, answer=answer_name, model_id=name, provider=spec['provider'], model=spec['model'], api=api)
        if slot:
            base['fallback'] = slot
        last_error = None
        for attempt in range(3):
            started = time.time()
            try:
                text, u, finish = caller(api)(client, spec, system, user, json_mode, images)
                with self.lock:
                    self.used += u['prompt_tokens'] + u['completion_tokens']
                    self.calls += 1
                self._receipt(**base, attempt=attempt, seconds=round(time.time() - started, 1), **u, finish=finish, status='ok')
                if self.progress:
                    self.progress.tokens(self.used, self.calls)
                if finish == 'content_filter':
                    answer = {'_refused': True}
                    self._event(f'{stage}：模型拒绝回答一块内容，已记入附录')
                else:
                    if finish != 'stop':
                        raise ValueError(f'回答没有正常结束：{finish}')
                    answer = json.loads(text) if json_mode else {'markdown': text}
                    if validate:
                        validate(answer)
                return answer
            except (openai.APIError, *am.ERRORS, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                last_error = e
                self._receipt(**base, attempt=attempt, seconds=round(time.time() - started, 1),
                              status='error', error=f'{type(e).__name__}: {str(e)[:300]}')
                self._event(f'{stage}：{name} 第 {attempt + 1} 次调用失败（{type(e).__name__}），{"稍后重试" if attempt < 2 else "不再用它"}')
                if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError, *am.AUTH_ERRORS)):
                    break    # a wrong key does not get better by retrying
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
        raise _Failed(last_error)

    @staticmethod
    def _usage(prompt, completion, cached=0, reasoning=0):
        return {'prompt_tokens': prompt or 0, 'completion_tokens': completion or 0, 'cached_tokens': cached or 0, 'reasoning_tokens': reasoning or 0}

    @classmethod
    def _responses(cls, client, spec, system, user, json_mode, images=None):
        """OpenAI Responses API: GPT-6 models need it for tool calls, so it is the default."""
        params = dict(spec.get('params', {}))
        if json_mode:
            # JSON mode looks for the word "json" in the input, not in the instructions.
            user = user + '\n\n（请按要求只输出 JSON 对象。）'
        kwargs = dict(model=spec['model'], instructions=system, input=user)
        if images:
            kwargs['input'] = [{'role': 'user', 'content': [{'type': 'input_text', 'text': user}]
                                + [{'type': 'input_image', 'image_url': url} for url in images]}]
        if 'reasoning_effort' in params:
            kwargs['reasoning'] = {'effort': params.pop('reasoning_effort')}
        kwargs.update(params)
        if json_mode:
            kwargs['text'] = {'format': {'type': 'json_object'}}
        r = client.responses.create(**kwargs)
        if r.status == 'completed':
            finish = 'stop'
        else:
            reason = getattr(r.incomplete_details, 'reason', None) or r.status
            finish = 'content_filter' if reason == 'content_filter' else reason
        u = r.usage
        usage = cls._usage(u.input_tokens, u.output_tokens,
                           getattr(getattr(u, 'input_tokens_details', None), 'cached_tokens', 0),
                           getattr(getattr(u, 'output_tokens_details', None), 'reasoning_tokens', 0)) if u else cls._usage(0, 0)
        return r.output_text or '', usage, finish

    @classmethod
    def _chat(cls, client, spec, system, user, json_mode, images=None):
        """OpenAI Chat Completions; images go as image_url parts after the text, the form vision models on this API take."""
        content = [{'type': 'text', 'text': user}] + [{'type': 'image_url', 'image_url': {'url': url}} for url in images] if images else user
        kwargs = dict(model=spec['model'], messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': content}])
        params = dict(spec.get('params', {}))
        kwargs.update(params)
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        r = client.chat.completions.create(**kwargs)
        u, choice = r.usage, r.choices[0]
        usage = cls._usage(u.prompt_tokens, u.completion_tokens,
                           getattr(getattr(u, 'prompt_tokens_details', None), 'cached_tokens', 0),
                           getattr(getattr(u, 'completion_tokens_details', None), 'reasoning_tokens', 0)) if u else cls._usage(0, 0)
        return choice.message.content or '', usage, choice.finish_reason
