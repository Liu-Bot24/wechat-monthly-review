/* 任务: the queue and past runs. Also the pieces a month's 运行 tab and a 专题 show: progress, masks, a month's runs. */
const ProgressView = {
  components: { Icon, Bar },
  props: { p: Object, live: Boolean },
  template: `
<div>
  <div class="row" style="gap:18px;align-items:flex-end;margin-bottom:12px">
    <div class="bigpct">{{ Math.round((p.fraction || 0) * 100) }}<span style="font-size:18px">%</span></div>
    <div class="grow">
      <div class="row small"><b>{{ p.message || stepLabel }}</b><span class="spacer"></span>
        <span class="muted" v-if="live && p.eta_seconds">预计还要 {{ fmtDur(p.eta_seconds) }}</span>
        <span class="muted" v-else-if="live">正在估计剩余时间…</span></div>
      <Bar :value="p.fraction" big :kind="barKind" style="margin-top:6px"/>
    </div>
  </div>
  <div class="kv" style="margin-bottom:14px">
    <div><div class="k">已用 token</div><div class="v">{{ fmtInt(p.tokens) }}</div></div>
    <div><div class="k">本次新调用</div><div class="v">{{ fmtInt(p.calls) }}</div></div>
    <div v-if="live"><div class="k">当前速度</div><div class="v">{{ speed ? fmtTok(speed) + ' / 分钟' : '—' }}</div></div>
    <div><div class="k">{{ live ? '已运行' : '用时' }}</div><div class="v">{{ fmtDur(elapsed) }}</div></div>
    <div v-if="p.budget"><div class="k">预算</div><div class="v">{{ fmtTok(p.budget) }}</div></div>
  </div>
  <div class="steps">
    <div class="step" v-for="s in p.steps" :key="s.key" :class="s.status">
      <div class="ic"><template v-if="s.status === 'done'">✓</template><template v-else-if="s.status === 'stopped'">!</template></div>
      <div class="lbl">{{ s.label }}<span class="muted small" v-if="s.status === 'skipped'"> · 跳过</span></div>
      <div><Bar v-if="s.total && s.status !== 'skipped'" :value="s.done / s.total" :kind="s.status === 'done' ? 'ok' : s.status === 'stopped' ? 'warn' : ''"/></div>
      <div class="small num">{{ s.total ? s.done + ' / ' + s.total : '' }}<span v-if="s.failed" style="color:var(--bad)"> · 失败 {{ s.failed }}</span></div>
      <div class="small muted num">{{ stepTime(s) }}</div>
    </div>
  </div>
</div>`,
  setup(props) {
    const now = ref(Date.now() / 1000);
    let t = setInterval(() => { now.value = Date.now() / 1000; }, 1000);
    onBeforeUnmount(() => clearInterval(t));
    const elapsed = computed(() => (props.live ? now.value : props.p.updated) - props.p.started);
    const stepLabel = computed(() => (props.p.steps || []).find(s => s.key === props.p.step)?.label || '');
    const barKind = computed(() => ({ failed: 'bad', 'budget-stop': 'warn', 'external-wait': 'warn', stopped: 'warn', done: 'ok', 'done-with-problems': 'warn' })[props.p.state] || '');
    const speed = computed(() => tokensPerMinute());
    function stepTime(s) {
      if (!s.started) return '';
      return fmtDur((s.ended || (props.live ? now.value : props.p.updated)) - s.started);
    }
    return { elapsed, stepLabel, barKind, speed, stepTime, fmtDur, fmtInt, fmtTok };
  },
};

// Makes the prompt for one agent out of the stages ticked: tick the stages that go to the same agent, copy, send;
// tick others for the next agent. stages: [{ name, tasks?, results? }], the stages handed out in the settings.
const PromptMaker = {
  components: { Icon },
  props: { stages: Array },
  template: `
<div>
  <div class="hand">
    <label class="hand-row" v-for="s in stages" :key="s.name">
      <input type="checkbox" :value="s.name" v-model="picked">
      <b>{{ s.name }}</b>
      <span class="grow muted" v-if="s.tasks !== undefined">{{ s.tasks ? s.tasks + ' 个任务，已交回 ' + s.results + ' 个' : '现在没有任务' }}</span>
    </label>
  </div>
  <div class="row wrap" style="margin:8px 0 6px">
    <button class="btn sm primary" :disabled="!picked.length || !prompt" @click="copy"><Icon name="copy"/>复制提示词</button>
    <span class="muted small">勾选要交给同一个 Agent 的环节，复制，发给它；换一组勾选，再复制，发给下一个 Agent。每个 Agent 发一次：它负责的环节之后再有任务，会接着做。</span>
  </div>
  <pre class="log" style="margin:0;max-height:220px">{{ picked.length ? prompt : '还没有勾选环节。' }}</pre>
</div>`,
  setup(props) {
    const names = () => props.stages.map(s => s.name);
    const picked = ref(names());            // every stage to begin with: one agent doing them all
    const prompt = ref('');
    watch(() => names().join('|'), () => { picked.value = picked.value.filter(n => names().includes(n)); if (!picked.value.length) picked.value = names(); });
    watch(picked, async now => {
      const chosen = names().filter(n => now.includes(n));      // in the order the run goes through them
      if (!chosen.length) { prompt.value = ''; return; }
      try {
        const r = await api('/api/external/prompt', { method: 'POST', body: { stages: chosen } });
        if (chosen.join('|') === names().filter(n => picked.value.includes(n)).join('|')) prompt.value = r.prompt;
      } catch (e) { fail(e); }
    }, { immediate: true, deep: true });
    async function copy() {
      try { await navigator.clipboard.writeText(prompt.value); toast(`已复制提示词（${names().filter(n => picked.value.includes(n)).join('、')}）`, 'ok'); } catch (e) { fail(e); }
    }
    return { picked, prompt, copy };
  },
};

// Tasks are waiting for external agents: what is the case, how many are back, and the prompt maker. h comes from the
// folders themselves (the server's _handoff), so it shows from the first task written.
const ExternalWait = {
  components: { Icon, PromptMaker },
  props: { h: Object, message: String },
  template: `
<div class="banner info" style="display:block">
  <div class="row" style="gap:10px;align-items:flex-start"><Icon name="info"/><div class="grow">{{ message }}</div>
    <span class="chip" :class="h.results >= h.tasks ? 'ok' : 'info'">已交回 {{ h.results }} / {{ h.tasks }}</span></div>
  <PromptMaker :stages="h.stages" style="margin-top:10px"/>
  <div class="row wrap" style="margin-top:10px">
    <button class="btn sm" @click="openPath(h.folder)"><Icon name="folder"/>打开交接文件夹</button>
    <span class="spacer"></span><slot></slot>
  </div>
</div>`,
  setup() { return { openPath }; },
};

// Masked values with their originals, read back from the archive; url is a month's or a 专题's masks endpoint.
const MaskModal = {
  components: { Icon, Modal },
  props: { url: String },
  emits: ['close'],
  template: `
<Modal title="被遮蔽的内容" wide @close="$emit('close')">
  <div class="muted small" style="margin-bottom:10px">发给模型之前，这些值被换成了类别名，模型没有看到原值。这里从归档读出原值给你核对，不会保存，也不会发出去。</div>
  <div v-if="!items" class="muted">{{ loading ? '正在读取…' : '这次没有记录遮蔽位置，重新估算或预览一次就能查看。' }}</div>
  <template v-else>
    <div class="row wrap" style="gap:6px;margin-bottom:10px">
      <button class="btn sm" :class="{ primary: !label }" @click="label = null">全部 {{ items.length }}</button>
      <button class="btn sm" v-for="(n, k) in groups" :key="k" :class="{ primary: label === k }" @click="label = k">{{ k }} {{ n }}</button>
    </div>
    <div style="max-height:62vh;overflow:auto">
      <table class="t small">
        <thead><tr><th>类别</th><th>原值</th><th>会话</th><th style="width:130px">时间</th><th class="r" style="width:190px"></th></tr></thead>
        <tbody>
          <template v-for="(it, i) in rows" :key="i">
            <tr>
              <td class="muted" style="white-space:nowrap">{{ it.label }}</td>
              <td><code class="mask-value">{{ it.value ?? '（没能从原消息中重新找到）' }}</code></td>
              <td>{{ it.name }}<span class="muted" v-if="it.self">（你发的）</span><span class="muted" v-if="!it.in_month">（所选时间之外，只作上下文）</span></td>
              <td class="num muted">{{ it.time }}</td>
              <td class="r" style="white-space:nowrap">
                <button class="btn sm ghost" @click="toggle(it)">{{ it.ctx ? '收起' : '上下文' }}</button>
                <button class="btn sm ghost" title="在归档的 Markdown 中打开这条消息" @click="openMessage(it.lookup_id)"><Icon name="ext"/>归档</button>
              </td>
            </tr>
            <tr v-if="it.ctx"><td></td><td colspan="4">
              <div v-if="it.ctx === 'loading'" class="muted">正在读取…</div>
              <div v-else v-for="m in it.ctx" :key="m.lookup_id" class="mask-ctx" :class="{ hit: m.lookup_id === it.lookup_id }">
                <span class="muted">{{ m.time.slice(5, 16) }} {{ m.sender }}：</span>{{ m.body }}</div>
            </td></tr>
          </template>
        </tbody>
      </table>
    </div>
  </template>
</Modal>`,
  setup(props) {
    const items = ref(null), loading = ref(true), label = ref(null);
    const groups = computed(() => {
      const g = {};
      for (const it of items.value || []) g[it.label] = (g[it.label] || 0) + 1;
      return g;
    });
    const rows = computed(() => (items.value || []).filter(it => !label.value || it.label === label.value));
    onMounted(async () => {
      try {
        const r = await api(props.url);
        // What you sent comes first, then the chosen time before the neighbouring context.
        const list = (r.items || []).map(it => reactive({ ...it, ctx: null }));
        list.sort((a, b) => (b.self - a.self) || (b.in_month - a.in_month) || a.time.localeCompare(b.time));
        items.value = r.items ? list : null;
      } catch (e) { fail(e); }
      loading.value = false;
    });
    async function toggle(it) {
      if (it.ctx) { it.ctx = null; return; }
      it.ctx = 'loading';
      try { it.ctx = (await api(`/api/context/${it.lookup_id}?n=3`)).messages; } catch (e) { it.ctx = null; fail(e); }
    }
    async function openMessage(lid) {
      try { await api('/api/open', { method: 'POST', body: { lookup_id: lid } }); } catch (e) { fail(e); }
    }
    return { items, loading, label, groups, rows, toggle, openMessage };
  },
};

// A job's log, read from its file; refreshes on demand.
const LogView = {
  components: { Icon, Modal },
  props: { path: String },
  emits: ['close'],
  template: `
<Modal title="日志" wide @close="$emit('close')">
  <pre class="log" ref="box">{{ text === null ? '正在读取…' : text || '（空）' }}</pre>
  <template #foot><button class="btn" @click="openPath(path, true)"><Icon name="folder"/>在资源管理器中显示</button>
    <button class="btn" @click="load"><Icon name="refresh"/>刷新</button></template>
</Modal>`,
  setup(props) {
    const text = ref(null), box = ref(null);
    async function load() {
      try {
        text.value = (await api('/api/jobs/log?path=' + encodeURIComponent(props.path))).text;
        await nextTick();
        if (box.value) box.value.scrollTop = box.value.scrollHeight;
      } catch (e) { fail(e); }
    }
    onMounted(load);
    return { text, box, load, openPath };
  },
};

function jobTitle(j) {
  if (j.kind === 'asr') return `语音转写 ${j.from} 至 ${j.to}`;
  if (j.kind.startsWith('topic-')) return `专题「${j.title || '未命名'}」· ${{ 'topic-plan': '预览', 'topic-screen': '模型筛查', 'topic-run': '生成' }[j.kind]}`;
  return `${monthLabel(j.month)} · ${j.kind === 'plan' ? '估算' : '生成月报'}`;
}
// Where a job's own page is: the month's 运行 tab, the 专题, or the voice page.
function goJob(j) {
  if (j.kind === 'asr') go('voice');
  else if (j.kind.startsWith('topic-')) go('topics', j.month.replace(/^专题\//, ''));
  else go('reports', j.month, { tab: 'run' });
}
function parseBudget(text) {
  const t = String(text || '').trim().toUpperCase().replace(/,/g, '');
  if (!t) return null;
  const m = t.match(/^(\d+(?:\.\d+)?)\s*([MK万]?)$/);
  if (!m) throw new Error('预算请写成数字，可以用 M 表示百万');
  return Math.round(+m[1] * ({ M: 1e6, K: 1e3, '万': 1e4 }[m[2]] || 1));
}
async function stopJob() {
  const ok = await confirmBox('停止运行', '停止以后，已经完成的调用都保存在缓存中，之后继续运行时不会重做。确定停止吗？', { ok: '停止', danger: true });
  if (!ok) return;
  try { store.jobs = await api('/api/jobs/stop', { method: 'POST', body: {} }); toast('已停止', 'warn'); } catch (e) { fail(e); }
}

// One month's runs, on the month's 运行 tab: the job in progress, the last run (and continuing it), the estimate.
const MonthRun = {
  components: { Icon, ProgressView, MaskModal, LogView, ExternalWait },
  props: { month: String, st: Object },
  template: `
<div class="col" style="gap:14px;align-items:stretch">
  <div class="card" v-if="cur">
    <div class="card-head">
      <span class="dot run"></span><h2>{{ cur.kind === 'plan' ? '正在估算' : '正在生成' }}</h2>
      <span class="muted small">开始于 {{ fmtTime(cur.started) }}</span>
      <span class="spacer"></span>
      <button class="btn sm" @click="log = cur.log"><Icon name="log"/>日志</button>
      <button class="btn sm danger" @click="stopJob"><Icon name="stop"/>停止</button>
    </div>
    <div class="card-body">
      <ExternalWait v-if="cur.handoff" :h="cur.handoff" :message="'这个月有 ' + cur.handoff.tasks + ' 个任务由外部 Agent 做。下面是接口这边的进度，两边互不耽误；要用到 Agent 的结果而它还没交回时，这个月停下来等，交齐后自动继续。'"/>
      <ProgressView v-if="cur.progress" :p="cur.progress" live/>
      <div v-else class="row muted"><span class="spinner"></span>{{ cur.kind === 'plan' ? '正在取数和分块（不调用模型）…' : '正在启动…' }}</div>
    </div>
  </div>
  <div class="banner warn" v-else-if="queued" style="margin:0"><Icon name="info"/><div class="grow">这个月份的任务在排队，前面的任务完成后开始。</div>
    <button class="btn sm" @click="go('run')">查看任务</button></div>

  <ExternalWait v-if="hand && !cur" :h="hand" :message="handText" style="margin:0">
    <button class="btn sm" :disabled="busy" title="不等 Agent：还没有结果的部分由各环节的模型完成，按接口计费" @click="resume(true)">改用模型做完</button>
  </ExternalWait>

  <div class="two">
    <div class="card" v-if="last && !cur">
      <div class="card-head"><h2>上次运行</h2>
        <span class="chip" :class="stateKind(last.state)">{{ stateText(last.state) }}</span>
        <span class="spacer"></span>
        <button class="btn sm ghost" v-if="lastLog" @click="log = lastLog"><Icon name="log"/>日志</button></div>
      <div class="card-body">
        <div class="banner warn" v-if="last.state === 'budget-stop'"><Icon name="warn"/><div class="grow">{{ last.message }}</div></div>
        <div class="banner bad" v-else-if="last.state === 'failed'"><Icon name="warn"/><div class="grow">{{ last.message || '运行失败，请查看日志。' }}</div></div>
        <div class="banner warn" v-if="repairable"><Icon name="warn"/>
          <div class="grow">
            <div>有 {{ failed.length }} 处调用失败，月报里缺了这部分内容。补做只重新调用失败的部分和用到它的成稿，其余直接用已有结果；完成后月报会更新。</div>
            <div class="muted small" v-for="f in failed.slice(0, 3)" :key="f" style="margin-top:4px">{{ f }}</div>
          </div>
          <button class="btn sm primary" @click="repair" :disabled="busy"><Icon name="play"/>补做失败的部分</button></div>
        <div class="row wrap" style="margin-bottom:12px" v-if="resumable">
          <span>从断点继续，这次的预算</span>
          <input class="input" style="width:120px" v-model="budgetText" placeholder="不限">
          <span class="muted small">单位可以写 M（百万），留空为这次不限</span>
          <span class="spacer"></span>
          <button class="btn primary" @click="resume()" :disabled="busy"><Icon name="play"/>继续</button>
        </div>
        <ProgressView :p="last"/>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>估算</h2>
        <span class="muted small" v-if="plan">{{ plan.made_at.replace('T', ' ') }}</span>
        <span class="chip warn" v-if="plan && plan.stale.length">之后{{ plan.stale.join('、') }}有变化，建议重新估算</span></div>
      <div class="card-body" v-if="plan">
        <div class="tiles" style="margin-bottom:10px">
          <div class="tile"><div class="k">预计用量</div><div class="v">{{ fmtTok(plan.estimate.tokens) }}</div><div class="d">{{ plan.estimate.basis }}</div></div>
          <div class="tile"><div class="k">预计时间</div><div class="v">{{ plan.estimate.minutes }} 分钟</div><div class="d">并发 {{ settings && settings.concurrency }}</div></div>
        </div>
        <div class="banner warn" v-if="plan.over_budget"><Icon name="warn"/><div class="grow">预计用量超过每月预算 {{ fmtTok(plan.budget) }}。运行到预算上限时会自动暂停，可以追加预算后继续。</div></div>
        <table class="t small">
          <tbody>
            <tr><td>读取会话</td><td class="r num">{{ fmtInt(plan.chats) }} 个</td><td>消息</td><td class="r num">{{ fmtInt(plan.messages) }} 条（我发 {{ fmtInt(plan.self_messages) }}）</td></tr>
            <tr><td>精读</td><td class="r num">{{ plan.chunks.read_a.n }} 块</td><td>扫读</td><td class="r num">{{ plan.chunks.scan_b.n }} 块</td></tr>
            <tr><td>@我</td><td class="r num">{{ plan.at_me }} 条</td><td>引用我</td><td class="r num">{{ plan.quote_me }} 条</td></tr>
            <tr><td>语音</td><td class="r num">{{ plan.voice.total }} 条</td><td>待转写</td><td class="r num">{{ plan.voice.pending }} 条</td></tr>
            <tr v-for="s in plan.skipped" :key="s.reason"><td>跳过</td><td colspan="3">{{ s.reason }}：{{ s.chats }} 个会话 / {{ fmtInt(s.messages) }} 条</td></tr>
            <tr v-if="handedText"><td>外部 Agent</td><td colspan="3">{{ handedText }}</td></tr>
            <tr v-if="maskText"><td>遮蔽</td><td colspan="3"><div class="row" style="gap:10px">
              <span class="grow">{{ maskText }}</span>
              <button class="btn sm" @click="masks = true" title="每一处被遮蔽的原值和它所在的消息；只在这台电脑上显示"><Icon name="search"/>查看原文</button></div></td></tr>
          </tbody>
        </table>
      </div>
      <div class="card-body empty" v-else>还没有估算过。估算只取数和分块，不调用模型，每个月大约 1–2 分钟。</div>
    </div>
  </div>

  <MaskModal v-if="masks" :url="'/api/months/' + month + '/masks'" @close="masks = false"/>
  <LogView v-if="log" :path="log" @close="log = null"/>
</div>`,
  setup(props) {
    const budgetText = ref(''), masks = ref(false), log = ref(null);
    const cur = computed(() => { const c = store.jobs.current; return c && c.month === props.month ? c : null; });
    const queued = computed(() => store.jobs.queue.some(j => j.month === props.month));
    const busy = computed(() => !!cur.value || queued.value);
    const settings = computed(() => store.settings?.settings);
    const plan = computed(() => props.st?.plan || null);
    const last = computed(() => props.st?.progress && props.st.progress.state !== 'running' ? props.st.progress : null);
    const lastLog = computed(() => store.jobs.history.find(r => r.month === props.month && r.kind === 'run')?.log || null);
    const resumable = computed(() => monthResumable(props.st));
    // What waits for an external agent, kept fresh while this page is open so the count of results moves as the agent works.
    const hand = ref(null);
    watch(() => props.st?.handoff, v => { hand.value = v || null; }, { immediate: true });
    const handTimer = setInterval(async () => {
      if (!hand.value || cur.value) return;
      try { hand.value = (await api(`/api/months/${props.month}/status`)).handoff || null; } catch (e) { /* the next tick asks again */ }
    }, 5000);
    onBeforeUnmount(() => clearInterval(handTimer));
    // What is the case, never an order to do things in: the month goes on by itself where it can.
    const handText = computed(() => {
      const n = hand.value?.tasks, state = last.value?.state;
      if (queued.value) return `这个月有 ${n} 个任务由外部 Agent 做。这个月已经在队列里，轮到时接着处理。`;
      if (state === 'external-wait') return `这个月在等外部 Agent：${n} 个任务。结果交齐后自动继续，不用回来点（消息月报的窗口开着时）。`;
      if (state) return `这个月有 ${n} 个任务由外部 Agent 做。上次运行${stateText(state)}，没有在等它；再生成时会用上已经交回的结果。`;
      return `这个月有 ${n} 个任务由外部 Agent 做，估算时已经写出。生成这个月时会用上它交回的结果，没交回的会等。`;
    });
    // What the estimate handed to the agent, or why it has not yet.
    const handedText = computed(() => {
      const x = plan.value?.external;
      if (!x) return '';
      const parts = Object.entries(x.handed || {}).map(([s, n]) => `${{ scan_b: '扫读', read_a: '精读' }[s] || s} ${n} 块`);
      const done = parts.length ? parts.join('、') + '已经写成任务，提示词在这一页上方' : x.after_voice ? '还有语音没有转写，任务在生成时转写完再写出' : '';
      const unset = (x.no_model || []).length ? `${x.no_model.join('、')}还没有选择模型，选好以后才会写出任务` : '';
      return [done, unset].filter(Boolean).join('；');
    });
    const repairable = computed(() => monthRepairable(props.st));
    // "read_a <key> 调用失败：Error code: 503 - {...}" → the status code and the innermost message (an upstream one sits inside the proxy's).
    const failed = computed(() => Object.values(props.st?.status?.failures || {}).map(v => {
      const code = /Error code: (\d+)/.exec(v), msg = /"message":\s*"([^"]+)"/.exec(v) || /'message':\s*'([^']+)'/.exec(v);
      return msg ? (code ? code[1] + '：' : '') + msg[1] : v.replace(/^\S+ \S+ /, '');
    }));
    async function repair() { await addJobs([{ kind: 'run', month: props.month }]); }
    const maskText = computed(() => Object.entries(plan.value?.masks || {}).map(([k, v]) => `${k} ${v}`).join('、'));
    watch(() => props.month, () => { budgetText.value = ''; });
    async function resume(apiOnly) {
      let budget;
      try { budget = parseBudget(budgetText.value); } catch (e) { return fail(e); }
      await addJobs([{ kind: 'run', month: props.month, budget: budget || 0, ...(apiOnly ? { api_only: true } : {}) }]);      // left empty: no cap this time
    }
    onMounted(() => { if (!store.settings) loadSettings(); });
    return { budgetText, masks, log, cur, queued, busy, settings, plan, last, lastLog, resumable, hand, handText, handedText, repairable, failed, repair, maskText, resume, stopJob, go,
             fmtTok, fmtInt, fmtTime, stateText, stateKind };
  },
};
function monthResumable(st) {
  const stopped = ['budget-stop', 'external-wait', 'failed', 'stopped'];
  return !!st && (stopped.includes(st.status?.state) || (st.progress && st.progress.state !== 'running' && stopped.includes(st.progress.state)));
}
// Finished, but some calls failed: running again asks only for those (and what is built from them), the rest is cached.
function monthRepairable(st) {
  return st?.status?.state === 'done-with-problems' && Object.keys(st.status.failures || {}).length > 0;
}

MRPages.run = {
  components: { Icon, ProgressView, LogView },
  template: `
<div class="page">
  <div class="page-head">
    <h1>任务</h1>
    <span class="sub">月报、专题和语音转写的任务按顺序一个一个运行。</span>
  </div>

  <div class="card" style="margin-bottom:14px" v-if="cur">
    <div class="card-head">
      <span class="dot run"></span><h2>{{ jobTitle(cur) }}</h2>
      <span class="muted small">开始于 {{ fmtTime(cur.started) }}</span>
      <span class="spacer"></span>
      <button class="btn sm" @click="goJob(cur)"><Icon name="ext"/>打开</button>
      <button class="btn sm" @click="log = cur.log"><Icon name="log"/>日志</button>
      <button class="btn sm danger" @click="stopJob"><Icon name="stop"/>停止</button>
    </div>
    <div class="card-body">
      <ProgressView v-if="cur.progress" :p="cur.progress" live/>
      <div v-else class="row muted"><span class="spinner"></span>{{ cur.kind === 'plan' || cur.kind === 'topic-plan' ? '正在取数（不调用模型）…' : '正在启动…' }}</div>
    </div>
  </div>

  <div class="two">
    <div class="card">
      <div class="card-head"><h2>队列</h2><span class="muted small">{{ jobs.queue.length ? jobs.queue.length + ' 个等待中' : '' }}</span>
        <span class="spacer"></span>
        <button class="btn sm" v-if="jobs.queue.length" @click="clearQueue">清空队列</button></div>
      <div v-if="jobs.queue.length">
        <table class="t"><tbody>
          <tr v-for="(j, i) in jobs.queue" :key="j.id">
            <td><a href="" @click.prevent="goJob(j)">{{ jobTitle(j) }}</a></td><td class="muted small">{{ j.budget ? '预算 ' + fmtTok(j.budget) : '' }}</td>
            <td class="r" style="white-space:nowrap">
              <button class="btn ghost sm icon" title="上移" :disabled="i === 0" @click="move(j, -1)"><Icon name="up"/></button>
              <button class="btn ghost sm icon" title="下移" :disabled="i === jobs.queue.length - 1" @click="move(j, 1)"><Icon name="down"/></button>
              <button class="btn ghost sm icon" title="移除" @click="remove(j)"><Icon name="x"/></button></td>
          </tr></tbody></table>
      </div>
      <div class="card-body empty" v-else>没有等待中的任务。</div>
    </div>
    <div class="card">
      <div class="card-head"><h2>运行记录</h2></div>
      <div style="max-height:620px;overflow:auto" v-if="jobs.history.length">
        <table class="t small">
          <thead><tr><th>任务</th><th>开始</th><th class="r">用时</th><th>结果</th><th class="r">token</th><th></th></tr></thead>
          <tbody><tr v-for="r in jobs.history" :key="r.id">
            <td><a href="" @click.prevent="goJob(r)">{{ jobTitle(r) }}</a></td><td class="num">{{ fmtTime(r.started) }}</td><td class="r num">{{ fmtDur(r.ended - r.started) }}</td>
            <td><span class="chip" :class="stateKind(r.state)" :title="r.message">{{ stateText(r.state) }}</span></td>
            <td class="r num">{{ fmtTok(r.tokens) }}</td>
            <td class="r"><button class="btn ghost sm icon" title="查看日志" @click="r.log ? log = r.log : toast('这次运行没有日志', 'warn')"><Icon name="log"/></button></td>
          </tr></tbody></table>
      </div>
      <div class="card-body empty" v-else>还没有从界面运行过任务。</div>
    </div>
  </div>
  <LogView v-if="log" :path="log" @close="log = null"/>
</div>`,
  setup() {
    // Old links to a month's runs open that month's 运行 tab.
    if (/^\d{4}-\d{2}$/.test(route.arg)) history.replaceState(null, '', `#/reports/${route.arg}?tab=run`), parseRoute();
    const log = ref(null);
    const jobs = computed(() => store.jobs);
    const cur = computed(() => store.jobs.current);
    async function clearQueue() {
      for (const j of [...store.jobs.queue]) await api(`/api/jobs/${j.id}`, { method: 'DELETE' });
      pollJobs();
    }
    async function move(j, d) { try { store.jobs = await api(`/api/jobs/${j.id}/move`, { method: 'POST', body: { delta: d } }); } catch (e) { fail(e); } }
    async function remove(j) { try { store.jobs = await api(`/api/jobs/${j.id}`, { method: 'DELETE' }); } catch (e) { fail(e); } }
    return { log, jobs, cur, clearQueue, move, remove, stopJob, jobTitle, goJob, toast, fmtTok, fmtTime, fmtDur, stateText, stateKind };
  },
};
