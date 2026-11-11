"""A whole month twice: every stage answered by its model, then the reading stages answered by an external agent
that writes what the model would have written. The two reports must say the same thing."""
import json, re, tempfile, unittest
from pathlib import Path
from unittest import mock
from archive_fixture import write_chat
from mr import ROOT, config, external, pipeline
from mr.llm import Models
from mr.pipeline import Pipeline, digest

MONTH = '2026-10'
HANDED = ['scan_b', 'read_a', 'recall_a']


def answer_for(stage, user):
    """What the stand-in model says: fixed by the stage and the text it was given, so either way of asking gets the same."""
    sids = re.findall(r'\bm\d+\b', user.split('## 已经写好的结论卡')[0])
    tag = digest(user)[:6]
    if stage == 'read_a':
        return {'chat_note': f'概况 {tag}', 'cards': [{'title': f'结论 {tag}', 'summary': f'摘要 {tag} [{sids[-1]}]', 'who': '张三', 'when': '10-02',
                                                   'msg_ids': [sids[-1]], 'importance': 4, 'confidence': '高', 'sensitivity': '正常'}]}
    if stage == 'recall_a':
        return {'cards': [{'title': f'补充 {tag}', 'summary': f'漏掉的 {tag}', 'who': '张三', 'when': '10-01',
                           'msg_ids': [sids[0]], 'importance': 3, 'confidence': '中', 'sensitivity': '正常'}]}
    if stage == 'scan_b':
        return {'note': f'群里在聊 {tag}', 'topics': [{'title': f'话题 {tag}', 'summary': f'要点 {tag}', 'msg_ids': [sids[0]], 'value': 4}],
                'links': [], 'images': [], 'escalations': []}
    if stage == 'verify':
        return {'results': [{'i': i, 'verdict': '对', 'note': ''} for i in range(user.count('=== 卡片 '))]}
    if stage == 'compose_category':
        return {'markdown': f'分类详情 {tag}：' + '、'.join(re.findall(r'结论 \w+|补充 \w+|话题 \w+', user))}
    if stage == 'compose_main':
        return {'overview': f'总览 {tag}', 'actions': '无', 'key_events': f'要事 {tag}', 'insights': f'洞察 {tag}'}
    if stage == 'topic_compose':
        return {'markdown': f'专题 {tag}'}
    raise AssertionError(f'没有准备 {stage} 的回答')


def ask(self, stage, key, name, system, user, json_mode, validate, images, slot, answer_name=None):
    """In place of Models._ask: the stand-in model's answer, checked and receipted as a real one is."""
    answer = answer_for(stage, user)
    if validate:
        validate(answer)
    self._receipt(stage=stage, key=key, answer=answer_name, model_id=name, provider='p', model='m-main', api='chat', attempt=0, seconds=0.1,
                  **self._usage(100, 10), finish='stop', status='ok')
    return answer


def play_agent(folder):
    """Answer every task in the handoff folder from its file alone, as the prompt tells an agent to."""
    done = 0
    for task in sorted(Path(folder).glob(f'**/{external.TASKS}/*.md')):
        text = task.read_text(encoding='utf-8')
        first, rest = text.split('\n\n【要求】\n\n', 1)
        user = rest.split('\n\n【用户消息】\n\n', 1)[1][:-1]
        result = Path(first[len('结果文件：'):])
        stage = next(s['key'] for s in config.STAGES if s['label'] == task.relative_to(folder).parts[0])     # the root holds a folder per stage
        answer = answer_for(stage, user)
        result.write_text(answer['markdown'] if result.suffix == '.md' else json.dumps(answer, ensure_ascii=False), encoding='utf-8')
        done += 1
    return done


def play_agent_in(root, name):
    """An agent given one stage's folder: it answers what is there and nothing else."""
    done = 0
    for task in sorted((Path(root) / name).glob(f'**/{external.TASKS}/*.md')):
        text = task.read_text(encoding='utf-8')
        result = Path(text.split('\n', 1)[0][len('结果文件：'):])
        if result.exists():
            continue
        stage = next(s['key'] for s in config.STAGES if s['label'] == name)
        result.write_text(json.dumps(answer_for(stage, text.split('\n\n【用户消息】\n\n', 1)[1][:-1]), ensure_ascii=False), encoding='utf-8')
        done += 1
    return done


def body(report):
    """The report without the lines that name who answered and what it cost."""
    text = re.sub(r'\n### 调用与用量\n[\s\S]*?(?=\n### )', '\n', report)
    return [l for l in text.splitlines() if not l.startswith(('> 模型：', '- 外部处理：'))]


class ExternalEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.archive = base / '归档'
        msg = lambda n, day, who, sid, text: (f'msg-{n:024x}', f'2026-10-{day:02d} 09:{n % 60:02d}:00', who, '文本', sid, '', [text], [])
        write_chat(self.archive, '01_私聊/张三', '张三', MONTH, 'zhang', 'me',
                   [msg(i, 1 + i // 4, '张三' if i % 2 else '我', 'zhang' if i % 2 else 'me', f'私聊第 {i} 句，谈合同和付款') for i in range(12)])
        write_chat(self.archive, '05_群/大群', '大群', MONTH, 'big@chatroom', 'me',
                   [msg(100 + i, 1 + i // 10, f'群友{i % 7}', f'u{i % 7}', f'群里第 {i} 句，讨论新模型的价格') for i in range(40)], members=300)
        home = mock.patch.object(config, 'HOME', base / 'home')
        home.start()
        self.addCleanup(home.stop)
        asked = mock.patch.object(Models, '_ask', ask)
        asked.start()
        self.addCleanup(asked.stop)
        pauses = mock.patch.object(pipeline, 'RETRY_PAUSES', (0, 0))       # a failing test must not sit out the retry pauses
        pauses.start()
        self.addCleanup(pauses.stop)
        p = {'base_url': 'http://127.0.0.1:9', 'api': 'chat', 'key': {'type': 'none'}}
        self.models = {'providers': {'p': p}, 'models': {'main': {'provider': 'p', 'model': 'm-main', 'params': {}, 'vision': False, 'price': {}, 'note': ''}},
                       'stages': {s: 'main' for s in config.EXTERNAL_STAGES}, 'fallback': {'enabled': False, 'models': ['', '']}}

    def month(self, out, **settings):
        """Run the month until it is done, playing the agent whenever the run waits. Returns (report, rounds of waiting, the last run)."""
        s = {**config.load_settings(), 'archive_root': str(self.archive), 'output_root': str(self.tmp.name + '/' + out),
             'asr': False, 'vision': False, 'notify': False, **settings}
        waits = 0
        for _ in range(6):
            pipe = Pipeline(ROOT, MONTH, s, self.models)
            self.addCleanup(pipe.archive.close)
            with mock.patch('builtins.print'):
                code = pipe.run()
            if code == 0:
                return (pipe.run_dir / f'月报_{MONTH}.md').read_text(encoding='utf-8'), waits, pipe
            self.assertEqual(code, 4)
            folder = external.root(s)
            self.assertFalse(external.check_report(folder)[1])
            self.assertGreater(play_agent(folder), 0)
            self.assertTrue(external.check_report(folder)[1])        # what the agent's own check passes, the run takes
            waits += 1
        self.fail('没有在几轮之内完成')

    def test_the_report_is_the_same_whoever_reads(self):
        by_models, waits, _ = self.month('模型')
        self.assertEqual(waits, 0)
        by_agent, waits, pipe = self.month('外部', external_stages=HANDED, external_audit=100)
        self.assertEqual(waits, 2)                                   # reading, then the recall that needs what was read
        self.assertEqual(body(by_agent), body(by_models))
        self.assertIn('话题 ', by_models)                            # both tiers were read, not an empty month
        self.assertIn('补充 ', by_models)
        self.assertNotIn('外部', by_models)
        self.assertIn('> 模型：扫读 外部 Agent · 精读 外部 Agent · 查漏 外部 Agent · 复核 m-main', by_agent)
        note = next(l for l in by_agent.splitlines() if l.startswith('- 外部处理：'))
        used = pipe.external_used
        self.assertEqual(set(used), set(HANDED))
        self.assertIn(f'抽查 {sum(used.values())} 块', note)          # every answer was sampled and none fell short
        self.assertNotIn('改用了模型的结果', note)
        self.assertFalse(external.root(pipe.settings).exists())                      # nothing left for an agent to do
        receipts = [json.loads(l) for l in (pipe.run_dir / 'receipts.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual({r['stage'] for r in receipts if r['api'] == 'external'}, set(HANDED))

    def test_the_estimate_hands_out_the_agents_tasks_before_anything_runs(self):
        s = {**config.load_settings(), 'archive_root': str(self.archive), 'output_root': str(self.tmp.name + '/先估算'),
             'asr': False, 'vision': False, 'notify': False, 'external_stages': ['scan_b']}
        def estimate(models, hand_out=True):
            pipe = Pipeline(ROOT, MONTH, s, models)
            self.addCleanup(pipe.archive.close)
            plan = pipe.plan(quiet=True, hand_out=hand_out)
            return pipe, plan, sorted(p.name for p in (external.root(s) / '扫读' / MONTH / external.TASKS).glob('*.md'))
        # A plain estimate only estimates; one made for a run that goes to the agent also writes the agent's tasks.
        _, plan, tasks = estimate(self.models, False)
        self.assertEqual((plan['external']['handed'], tasks), ({}, []))
        pipe, plan, tasks = estimate(self.models)
        scans = sum(c['stage'] == 'scan_b' for c in pipe.chunks)
        self.assertGreater(scans, 0)
        self.assertEqual((plan['external'], len(tasks)), ({'handed': {'scan_b': scans}, 'after_voice': False, 'no_model': []}, scans))
        self.assertFalse((pipe.run_dir / 'receipts.jsonl').exists())                 # nothing was asked of any model
        # Another model for the stage, or leftovers of other instructions: the agent's files stay the same set.
        stale = external.root(s) / '扫读' / MONTH / external.TASKS / 'old-00000000.md'
        stale.write_text('旧任务', encoding='utf-8')
        other = json.loads(json.dumps(self.models))
        other['models']['second'] = {**other['models']['main'], 'model': 'm-second'}
        other['stages']['scan_b'] = 'second'
        self.assertEqual(estimate(other)[2], tasks)
        # The run: the model's part of the step is named for what it does, and every task was there before the first call.
        seen = []
        def watched(models, *a, **k):
            seen.append(len(list((external.root(s) / '扫读' / MONTH / external.TASKS).glob('*.md'))))
            return ask(models, *a, **k)
        run = Pipeline(ROOT, MONTH, s, self.models)
        self.addCleanup(run.archive.close)
        with mock.patch.object(Models, '_ask', watched), mock.patch('builtins.print'):
            self.assertEqual(run.run(), 4)
        self.assertTrue(seen and set(seen) == {scans})
        step = next(x for x in json.loads((run.run_dir / 'progress.json').read_text(encoding='utf-8'))['steps'] if x['key'] == 'read')
        self.assertEqual((step['label'], step['total'], step['done']), ('精读', len(run.chunks) - scans, len(run.chunks) - scans))
        self.assertIn(f'扫读 {scans} 块', run.progress.state['message'])

    def test_an_estimate_before_any_model_is_chosen_still_works(self):
        """A first-time user estimates before choosing models: the estimate is made. A stage handed to an agent gets
        no task until it has a model, and the plan names it; generating says which stages lack a model."""
        s = {**config.load_settings(), 'archive_root': str(self.archive), 'output_root': str(self.tmp.name + '/没选模型'),
             'asr': False, 'vision': False, 'notify': False, 'external_stages': ['scan_b', 'read_a']}
        blank = {'providers': {}, 'models': {}, 'stages': {}, 'fallback': {'enabled': False, 'models': ['', '']}}
        pipe = Pipeline(ROOT, MONTH, s, blank)
        self.addCleanup(pipe.archive.close)
        plan = pipe.plan(quiet=True, hand_out=True)
        self.assertGreater(plan['est_tokens'], 0)
        self.assertEqual(plan['external'], {'handed': {}, 'after_voice': False, 'no_model': ['扫读', '精读']})
        self.assertEqual(list(external.root(s).glob('**/*.md')), [])
        run = Pipeline(ROOT, MONTH, s, blank)
        self.addCleanup(run.archive.close)
        with mock.patch('builtins.print'), self.assertRaises(SystemExit) as stop:
            run.run()
        self.assertIn('这些环节还没有选择模型：扫读、精读', str(stop.exception))

    def settings(self, out, **more):
        """Settings as the window keeps them, so the command line and the server find the same folders."""
        s = {**config.load_settings(), 'archive_root': str(self.archive), 'output_root': str(self.tmp.name + '/' + out),
             'asr': False, 'vision': False, 'notify': False, **more}
        config._write(config.settings_path(), s)
        config._save_models(self.models)
        return s

    def run_once(self, s):
        pipe = Pipeline(ROOT, MONTH, s, self.models)
        self.addCleanup(pipe.archive.close)
        with mock.patch('builtins.print'):
            return pipe.run(), pipe

    def test_results_that_arrive_while_the_run_works_are_taken_in_without_stopping(self):
        """The agent may be handed the prompt at any moment. Here it answers while the models are still reading:
        the run does not stop to wait, and nothing that follows from the agent's answers is left out."""
        by_models, _, _ = self.month('模型')
        s = self.settings('同时', external_stages=['scan_b'])
        folder = external.root(s)
        played = []
        def working(models, *a, **k):
            if not played:
                played.append(play_agent(folder))            # the agent finishes during the first model call
            return ask(models, *a, **k)
        with mock.patch.object(Models, '_ask', working):
            code, pipe = self.run_once(s)
        self.assertEqual((code, played[0] > 0), (0, True))
        self.assertEqual(pipe.external_used['scan_b'], played[0])
        self.assertEqual(body((pipe.run_dir / f'月报_{MONTH}.md').read_text(encoding='utf-8')), body(by_models))

    def test_a_waiting_month_goes_on_by_itself_and_the_agent_stays_for_the_next_batch(self):
        """Estimate or run first, agent early or late: the month stops only where a result is missing, is queued again
        once the results are in, and the agent's check tells it to wait for the next batch, then that it can end."""
        from mr import server
        from mr.__main__ import main
        s = self.settings('自动', external_stages=HANDED)
        root = str(external.root(s))
        queued = []
        check = lambda: main(['check', root, '--wait', '0'])
        fake = mock.Mock(add=lambda items: queued.extend(items) or items, busy=lambda: False)
        with mock.patch('builtins.print'), mock.patch.object(server, 'jobs', fake), mock.patch.object(server, '_resumed', {}):
            self.assertEqual(check(), 0)                         # the agent was started before anything: nothing to do, nothing coming
            self.assertEqual(self.run_once(s)[0], 4)             # the month stops where the first results are missing
            server._resume_waiting()
            self.assertEqual((queued, check()), ([], 1))         # not continued while results are missing; the agent has work
            play_agent(root)
            self.assertEqual(check(), 3)                         # all pass, and the month will go on: the agent waits for more
            server._resume_waiting(); server._resume_waiting()
            self.assertEqual(queued, [{'kind': 'run', 'month': MONTH, 'api_only': False}])      # queued once, for the agent again
            self.assertEqual(self.run_once(s)[0], 4)             # it goes on, and stops for the second batch
            self.assertEqual(check(), 1)                         # which the waiting agent is told about
            play_agent(root)
            server._resume_waiting()
            self.assertEqual(len(queued), 2)
            code, pipe = self.run_once(s)
            self.assertEqual((code, check()), (0, 0))            # done: the agent is told it can end
        self.assertFalse(Path(root).exists())

    def test_stages_are_shared_out_among_agents_as_the_user_picks(self):
        """A prompt is made for whatever stages are picked. Here scanning goes to one agent, careful reading and the
        second reader to another: each sees only its own folders, is told about its own tasks, waits for the batch
        still to come in its folders, and is told it can end when nothing more can come there."""
        from mr import server
        from mr.__main__ import main
        s = self.settings('分开', external_stages=['scan_b', 'read_a', 'recall_a'])
        root = external.root(s)
        prompt = lambda *names: server.external_prompt({'stages': list(names)})['prompt']
        check = lambda *names: main(['check', *[str(root / n) for n in names], '--wait', '0'])
        with mock.patch('builtins.print'):
            self.assertEqual(self.run_once(s)[0], 4)
            hand = server._handoff(MONTH)
            self.assertEqual([st['name'] for st in hand['stages']], ['扫读', '精读', '查漏'])       # every stage handed out, with tasks or not yet
            scan, read, recall = hand['stages']
            self.assertEqual((hand['tasks'], recall['tasks'], hand['folder']), (scan['tasks'] + read['tasks'], 0, str(root)))
            one = prompt('扫读')
            self.assertIn(f'处理这个文件夹里的阅读任务：{root / "扫读"}\n', one)
            self.assertIn(f'check "{root / "扫读"}" --wait', one)
            self.assertNotIn('精读', one)
            two = prompt('精读', '查漏')
            self.assertIn(f'处理这些文件夹里的阅读任务：\n  {root / "精读"}\n  {root / "查漏"}\n', two)
            self.assertIn(f'check "{root / "精读"}" "{root / "查漏"}" --wait', two)
            self.assertNotIn('扫读', two)
            with self.assertRaises(server.HTTPException):
                prompt('没有这个环节')
            self.assertEqual((check('扫读'), check('精读', '查漏')), (1, 1))
            self.assertEqual(play_agent_in(root, '扫读'), scan['tasks'])
            self.assertEqual((check('扫读'), check('精读', '查漏')), (0, 1))        # the scanning agent is done; the other has work
            self.assertEqual(play_agent_in(root, '精读'), read['tasks'])
            self.assertEqual(check('精读', '查漏'), 3)                             # its folders will get the second reader's tasks: it waits
            self.assertEqual(self.run_once(s)[0], 4)                              # the month goes on and hands those out
            self.assertEqual(check('精读', '查漏'), 1)
            self.assertGreater(play_agent_in(root, '查漏'), 0)
            code, pipe = self.run_once(s)
            self.assertEqual((code, check('精读', '查漏')), (0, 0))
        self.assertEqual(set(pipe.external_used), {'scan_b', 'read_a', 'recall_a'})

    def test_tasks_kept_the_earlier_way_are_moved_to_their_stage(self):
        s = self.settings('旧布局', external_stages=['scan_b'])
        root = external.root(s)
        with mock.patch('builtins.print'):
            self.assertEqual(self.run_once(s)[0], 4)
        new, old = root / '扫读' / MONTH, root / MONTH / 'scan_b'
        names = sorted(p.name for p in (new / external.TASKS).iterdir())
        for sub in (external.TASKS, external.CHECKS):                # as an earlier version left them: <root>/<month>/<stage>
            (old / sub).mkdir(parents=True)
            for f in list((new / sub).iterdir()):
                text = f.read_text(encoding='utf-8')
                (old / sub / f.name).write_text(text.replace(str(new), str(old)) if sub == external.TASKS else text, encoding='utf-8')
                f.unlink()
        for task in (old / external.TASKS).iterdir():                # an agent on the old folder: its results go to the old place
            text = task.read_text(encoding='utf-8')
            Path(text.split('\n', 1)[0][len('结果文件：'):]).parent.mkdir(exist_ok=True)
            Path(text.split('\n', 1)[0][len('结果文件：'):]).write_text(
                json.dumps(answer_for('scan_b', text.split('\n\n【用户消息】\n\n', 1)[1][:-1]), ensure_ascii=False), encoding='utf-8')
        self.assertTrue(all(f.parent == old / external.RESULTS for f in root.glob(f'**/{external.RESULTS}/*.json')))
        found = external.owner_tally(s, MONTH)                       # the window looking at the month is enough to move them
        self.assertEqual((found['tasks'], found['results'], [st['name'] for st in found['stages']]), (len(names), len(names), ['扫读']))
        self.assertFalse((root / MONTH).exists())
        self.assertTrue((new / external.RESULTS).is_dir())
        first = (new / external.TASKS / names[0]).read_text(encoding='utf-8').splitlines()[0]
        self.assertEqual(first, f'结果文件：{new / external.RESULTS / (Path(names[0]).stem + ".json")}')
        with mock.patch('builtins.print'):
            code, pipe = self.run_once(s)
        self.assertEqual((code, pipe.external_used['scan_b']), (0, len(names)))

    def test_a_topic_given_to_the_agent_gets_its_tasks_before_its_run(self):
        """A 专题 whose run waits behind another job: its estimate writes the agent's reading tasks at once; a plain
        estimate writes none; the run that follows finds those tasks and writes no others."""
        from mr import topic
        s = self.settings('专题', external_stages=['read_a'])
        spec = {**topic.blank(), 'title': '合同', 'range': {'unit': 'month', 'from': MONTH + '-01', 'to': MONTH + '-31'},
                'scope': {**topic.new_scope(), 'follow_monthly': True},
                'topic': {'mode': 'text', 'description': '', 'keywords': ['合同'], 'window': 15}}
        tid = topic.create(s, spec)
        def made(**kw):
            run = topic.TopicRun(ROOT, tid, s, self.models)
            self.addCleanup(run.archive.close)
            with mock.patch('builtins.print'):
                return run, (run.plan(**kw) if 'hand_out' in kw else run.run())
        tasks = lambda: sorted(p.name for p in external.root(s).glob(f'**/{external.TASKS}/*.md'))
        made(hand_out=False)
        self.assertEqual(tasks(), [])
        run, _ = made(hand_out=True)
        self.assertGreater(len(run.chunks), 0)
        handed = tasks()
        self.assertEqual(len(handed), len(run.chunks))
        self.assertFalse((run.run_dir / 'receipts.jsonl').exists())                 # nothing was asked of any model
        run, code = made()
        self.assertEqual((code, tasks()), (4, handed))
        play_agent(external.root(s))
        run, code = made()
        self.assertEqual((code, run.external_used['topic_read']), (0, len(handed)))

    def test_tasks_an_agent_is_working_on_keep_their_names(self):
        """Tasks written under an earlier naming, for the same blocks and instructions, stay as they are: the results
        the agent writes for them are found, and no second set of tasks appears beside them."""
        s = {**config.load_settings(), 'archive_root': str(self.archive), 'output_root': str(self.tmp.name + '/旧名'),
             'asr': False, 'vision': False, 'notify': False, 'external_stages': ['scan_b']}
        def estimate():
            pipe = Pipeline(ROOT, MONTH, s, self.models)
            self.addCleanup(pipe.archive.close)
            pipe.plan(quiet=True, hand_out=True)
            return pipe
        pipe = estimate()
        base = external.root(s) / '扫读' / MONTH
        for task in list((base / external.TASKS).glob('*.md')):
            old = task.stem.rsplit('-', 1)[0] + '-0ld0ld00'
            (base / external.TASKS / (old + '.md')).write_text(task.read_text(encoding='utf-8').replace(task.stem, old), encoding='utf-8')
            (base / external.CHECKS / (task.stem + '.json')).rename(base / external.CHECKS / (old + '.json'))
            task.unlink()
        other = base / external.TASKS / 'k-0ther000.md'              # another block's leftover under other instructions
        other.write_text(external.task_text(base / external.RESULTS / 'k-0ther000.json', '别的要求', 'm1 别的材料'), encoding='utf-8')
        names = sorted(p.stem for p in (base / external.TASKS).glob('*.md') if p != other)
        self.assertEqual(play_agent(external.root(s)), len(names) + 1)
        estimate()                                                   # e.g. the user estimates again while the agent works
        self.assertEqual(sorted(p.stem for p in (base / external.TASKS).glob('*.md')), names)
        self.assertEqual(sorted(p.stem for p in (base / external.RESULTS).glob('*.json')), names)
        run = Pipeline(ROOT, MONTH, s, self.models)
        self.addCleanup(run.archive.close)
        with mock.patch('builtins.print'):
            self.assertEqual(run.run(), 0)                           # every result was taken in: nothing waits
        self.assertEqual(run.external_used['scan_b'], len(names))

    def test_a_rerun_leaves_only_what_the_new_report_cost(self):
        """Read again after the archive changed: the receipts, and with them the usage page, are the new version's."""
        _, _, first = self.month('模型')
        receipts = first.run_dir / 'receipts.jsonl'
        before = [json.loads(l) for l in receipts.read_text(encoding='utf-8').splitlines()]
        again, _, second = self.month('模型')                             # nothing changed: every answer comes from the cache
        self.assertEqual([json.loads(l) for l in receipts.read_text(encoding='utf-8').splitlines()], before)
        self.assertEqual(second.usage(), first.usage())
        doc = self.archive / '01_私聊/张三/聊天记录/2026-10.md'
        doc.write_text(doc.read_text(encoding='utf-8') + '\n' + '\n'.join(
            ['<a id="msg-%024x"></a>' % 900, '### 2026-10-20 10:00:00 · 张三 · 文本', '', '发送者：zhang', '', '> 又说了一句新的', '']), encoding='utf-8')
        _, _, third = self.month('模型')                                  # the private chat's block is new, so it is read again
        after = [json.loads(l) for l in receipts.read_text(encoding='utf-8').splitlines()]
        count = lambda rows, stage: sum(r['stage'] == stage for r in rows)
        self.assertEqual(count(after, 'read_a'), count(before, 'read_a'))            # the old block's call is gone, the new one is in
        self.assertEqual(count(after, 'scan_b'), count(before, 'scan_b'))            # the group was not touched: its call stays
        self.assertNotEqual({r['key'] for r in after if r['stage'] == 'read_a'}, {r['key'] for r in before if r['stage'] == 'read_a'})
        self.assertEqual(len(after), len(before))
        self.assertTrue(all(r['status'] == 'ok' for r in after))


if __name__ == '__main__':
    unittest.main()
