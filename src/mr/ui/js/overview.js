/* 总览: pick a month (or a range) to estimate or generate, and the months that already have something. */
MRPages.overview = {
  components: { Icon, Bar, SourceModal },
  template: `
<div class="page">
  <div class="page-head">
    <h1>总览</h1>
    <span class="sub" v-if="o">最新消息 {{ o.cutoff_text }}</span>
    <span class="spacer"></span>
    <button class="btn" @click="refresh"><Icon name="refresh"/>刷新</button>
  </div>
  <div class="banner warn" v-if="o && o.missing_stages.length">
    <Icon name="warn"/><div class="grow">这些环节还没有选择模型：{{ o.missing_stages.join('、') }}。生成月报之前，请先在设置中选择。</div>
    <a class="btn sm" href="#/settings/models">去设置</a>
  </div>
  <div class="tiles" v-if="o">
    <div class="tile"><div class="k">最近一期月报</div><div class="v">{{ latest ? monthLabel(latest.month) : '还没有' }}</div>
      <div class="d" v-if="latest">{{ stateText(latest.state) }} · {{ fmtTok(latest.tokens) }} token</div></div>
    <div class="tile"><div class="k">最近 3 期平均用量</div><div class="v">{{ o.average_tokens ? fmtTok(o.average_tokens) : '—' }}</div>
      <div class="d">按已完成的月份计算</div></div>
    <div class="tile"><div class="k">每月预算</div><div class="v">{{ o.budget ? fmtTok(o.budget) : '不限' }}</div>
      <div class="d" v-if="o.suggested_budget">建议 {{ fmtTok(o.suggested_budget) }}（平均值的 2.25 倍）
        <a v-if="o.budget !== o.suggested_budget" href="" @click.prevent="setBudget">设为预算</a></div>
      <div class="d" v-else>完成几期以后会给出建议值</div></div>
    <div class="tile"><div class="k">已生成</div><div class="v">{{ reportCount }} 期</div>
      <div class="d">{{ staleCount ? staleCount + ' 期的数据源有变化' : '数据源都没有变化' }}</div></div>
  </div>

  <div class="card" v-if="o" style="margin-bottom:14px">
    <div class="card-head"><h2>生成月报</h2><span class="muted small">只能选已经结束的月份</span></div>
    <div style="padding:14px 16px">
      <div class="row" style="flex-wrap:wrap;gap:10px">
        <span>从</span>
        <select class="input" v-model="first" style="width:170px">
          <option v-for="m in choices" :key="m.month" :value="m.month">{{ monthLabel(m.month) }}{{ m.report ? '（已有）' : '' }}</option>
        </select>
        <span>到</span>
        <select class="input" v-model="last" style="width:170px">
          <option v-for="m in choices" :key="m.month" :value="m.month">{{ monthLabel(m.month) }}{{ m.report ? '（已有）' : '' }}</option>
        </select>
        <span class="muted small">{{ rangeText }}</span>
        <span class="spacer"></span>
        <button class="btn" @click="estimate(range)" :disabled="!range.length"><Icon name="info"/>估算（不调用模型）</button>
        <button class="btn primary" @click="generate(range)" :disabled="!range.length"><Icon name="play"/>生成{{ range.length > 1 ? '（从近到远排队）' : '' }}</button>
      </div>
      <div class="muted small" style="margin-top:8px">估算只取数和分块，每个月大约 1–2 分钟，不消耗 token；结果在月报页的"运行"里。</div>
    </div>
  </div>

  <div class="card" v-if="o">
    <div class="card-head"><h2>月报</h2><span class="muted small" v-if="rows.length">共 {{ rows.length }} 个月</span>
      <span class="spacer"></span>
      <button class="btn sm" v-if="staleRows.length" @click="generate(staleRows.map(m => m.month))"><Icon name="refresh"/>重新生成数据源有变化的（{{ staleRows.length }}）</button>
    </div>
    <table class="t" v-if="rows.length">
      <thead><tr><th style="width:150px">月份</th><th>状态</th><th class="r" style="width:120px">用量</th><th style="width:150px">更新时间</th><th class="r" style="width:280px"></th></tr></thead>
      <tbody>
        <tr v-for="m in rows" :key="m.month" @dblclick="m.report && go('reports', m.month)">
          <td><b>{{ monthLabel(m.month) }}</b></td>
          <td>
            <span class="chip accent" v-if="m.job === 'run'"><span class="dot run"></span>{{ runText }}</span>
            <span class="chip info" v-else-if="m.job === 'queued'">排队中</span>
            <span class="chip info" v-else-if="m.job === 'plan'">估算中</span>
            <span class="chip" :class="stateKind(m.state)" v-else-if="m.state">{{ stateText(m.state) }}</span>
            <span class="chip" v-else>已估算，未生成</span>
            <span class="chip warn click" v-if="m.stale.length" role="button" title="看看变了什么，再决定要不要重新生成" style="margin-left:6px" @click="source = m.month">数据源有变化</span>
          </td>
          <td class="r num">
            <template v-if="m.tokens">{{ fmtTok(m.tokens) }}</template>
            <span class="muted" v-else-if="m.estimate" title="估算值">约 {{ fmtTok(m.estimate.tokens) }}</span>
            <span class="muted" v-else>—</span>
          </td>
          <td class="muted small">{{ m.updated ? m.updated.replace('T', ' ').slice(0, 16) : '—' }}</td>
          <td class="r">
            <div class="row" style="justify-content:flex-end;gap:6px">
              <button class="btn sm primary" v-if="m.report" @click="go('reports', m.month)"><Icon name="reports"/>打开</button>
              <button class="btn sm" v-if="!m.job && (m.stale.length || !m.report)" @click="generate([m.month])"><Icon name="play"/>{{ m.report ? '重新生成' : '生成' }}</button>
              <button class="btn sm ghost" @click="go('reports', m.month, { tab: 'run' })">详情</button>
              <button class="btn sm ghost icon" v-if="!m.job" title="删除这个月的月报" @click="deleteMonth(m.month, m.tokens)"><Icon name="trash"/></button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="empty" v-else>还没有生成或估算过月报。在上面选择月份开始。</div>
  </div>
  <div class="banner bad" v-if="!o && store.overviewError"><Icon name="info"/><div class="grow">{{ store.overviewError }}</div>
    <button class="btn sm" @click="go('settings', 'general')">打开设置</button><button class="btn sm" @click="refresh">重试</button></div>
  <div class="empty" v-else-if="!o"><span class="spinner"></span> {{ store.overviewReading || '正在读取归档…' }}</div>
  <SourceModal v-if="source" :month="source" @close="source = null"/>
</div>`,
  setup() {
    const o = computed(() => store.overview);
    const months = computed(() => o.value?.months || []);
    const byMonth = computed(() => Object.fromEntries(months.value.map(m => [m.month, m])));
    // Newest first, only months whose data is fully exported.
    const choices = computed(() => months.value.filter(m => m.data === 'complete').slice().reverse());
    const rows = computed(() => months.value.filter(m => m.report || m.state || m.job || m.estimate).slice().reverse());
    const staleRows = computed(() => rows.value.filter(m => m.report && m.stale.length && !m.job));
    const latest = computed(() => [...months.value].reverse().find(m => m.report));
    const reportCount = computed(() => months.value.filter(m => m.report).length);
    const staleCount = computed(() => months.value.filter(m => m.stale.length).length);

    // Default to the newest month that has no report yet.
    const first = ref(''), last = ref(''), source = ref(null);
    watch(choices, cs => {
      if (!cs.length || (first.value && byMonth.value[first.value])) return;
      const m = (cs.find(x => !x.report) || cs[0]).month;
      first.value = last.value = m;
    }, { immediate: true });
    const range = computed(() => {
      if (!first.value || !last.value) return [];
      const [a, b] = [first.value, last.value].sort();
      return choices.value.map(m => m.month).filter(m => m >= a && m <= b);   // newest first
    });
    const rangeText = computed(() => {
      const n = range.value.length;
      if (!n) return '';
      const had = range.value.filter(m => byMonth.value[m].report).length;
      const est = range.value.map(m => byMonth.value[m].estimate);
      const tok = est.every(Boolean) ? est.reduce((s, e) => s + e.tokens, 0) : null;
      return [n > 1 ? `共 ${n} 个月` : '', had ? `${had} 个已有月报` : '', tok ? `估算约 ${fmtTok(tok)} token` : ''].filter(Boolean).join('，');
    });
    const runText = computed(() => {
      const p = store.jobs.current?.progress;
      return p ? `运行中 ${Math.round((p.fraction || 0) * 100)}%` : '运行中';
    });

    async function estimate(list) {
      await addJobs(list.map(month => ({ kind: 'plan', month })));
      toast('已加入估算。估算只取数和分块，不调用模型，每个月大约 1–2 分钟。', 'info');
      if (list.length === 1) go('reports', list[0], { tab: 'run' });
    }
    async function generate(list) {
      const done = list.map(m => byMonth.value[m]).filter(m => m.report && !m.stale.length);
      const body = done.length ? `${done.map(m => monthLabel(m.month)).join('、')} 已经有最新的月报。重新生成会直接使用缓存，几乎不消耗 token，但会覆盖现有的月报文件。` : '';
      const way = await chooseWay(done.length ? '重新生成' : list.length > 1 ? `生成 ${list.length} 个月` : `生成 ${monthLabel(list[0])}`,
                                  { body, where: list.length > 1 ? '各月份的"运行"里' : '这个月份的"运行"里' });
      if (!way) return;
      const r = await addJobs(list.map(month => ({ kind: 'run', month, agent: way === 'agent' })));
      if (r && r.added.length) go('reports', list[0], { tab: 'run' });
    }
    async function setBudget() {
      try {
        await api('/api/settings', { method: 'PUT', body: { budget_tokens: o.value.suggested_budget } });
        toast(`每月预算已设为 ${fmtInt(o.value.suggested_budget)} token`, 'ok');
        loadOverview();
      } catch (e) { fail(e); }
    }
    function refresh() { loadOverview(); }
    onMounted(loadOverview);
    return { store, o, choices, rows, staleRows, latest, reportCount, staleCount, first, last, source, range, rangeText, runText,
             estimate, generate, setBudget, refresh, go, deleteMonth, fmtTok, stateText, stateKind, monthLabel };
  },
};
