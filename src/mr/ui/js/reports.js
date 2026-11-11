/* 月报: one month's workspace. 运行: estimate, generate, continue; 结果: read the report, #numbers open the original messages beside it. */
const ContextView = {
  components: { Icon },
  props: { lid: Number },
  emits: ['close'],
  template: `
<div class="ctx">
  <div class="ctx-head">
    <div class="grow" v-if="data">
      <div style="font-weight:600" class="ellipsis" :title="data.name">{{ data.name }}</div>
      <div class="muted small">{{ data.category }} · {{ data.group ? '群聊' : '私聊' }} · #{{ lid }}</div>
    </div>
    <div class="grow muted" v-else>原文 #{{ lid }}</div>
    <button class="btn sm" title="在归档的 Markdown 中打开这条消息" @click="openArchive"><Icon name="ext"/>在归档中打开</button>
    <button class="btn ghost sm icon" title="关闭" @click="$emit('close')"><Icon name="x"/></button>
  </div>
  <div class="ctx-list" ref="list">
    <div v-if="loading" class="empty"><span class="spinner"></span></div>
    <div v-else-if="error" class="empty">{{ error }}</div>
    <template v-else-if="data">
      <div class="row" style="justify-content:center;margin-bottom:6px" v-if="n < 60"><button class="btn sm" @click="more">显示更多前后消息</button></div>
      <div v-for="m in data.messages" :key="m.lookup_id + '-' + m.ts" class="msg" :class="{me: m.self, target: m.lookup_id === lid, sys: m.type === '系统消息'}" :ref="el => { if (m.lookup_id === lid) target = el }">
        <div class="who">{{ m.self ? '你' : m.sender }} · {{ m.time.slice(5, 16) }}<span v-if="m.type !== '文本' && m.type !== '系统消息'"> · {{ m.type }}</span></div>
        <div class="bub">
          <template v-for="x in m.media" :key="x.id">
            <img v-if="x.kind === 'image'" class="thumb" :src="mediaUrl(x.id, true)" loading="lazy" @click="ui.lightbox = mediaUrl(x.id)" :title="x.name" @error="e => e.target.replaceWith(document.createTextNode('〔图片无法预览〕'))">
            <template v-else-if="x.kind === 'video'">
              <img class="thumb" :src="mediaUrl(x.id, true)" loading="lazy" title="用默认程序播放" @click="openMedia(x)" @error="e => e.target.replaceWith(document.createTextNode('〔视频〕'))">
            </template>
            <template v-else-if="x.kind === 'voice'">
              <audio controls preload="none" :src="mediaUrl(x.id)"></audio>
              <div class="tr" v-if="x.transcript">转写：{{ x.transcript }}</div>
              <div class="tr muted" v-else>还没有转写</div>
            </template>
            <div v-else><a href="" @click.prevent="openMedia(x)">📎 {{ x.name }}</a></div>
          </template>
          <div v-if="showBody(m)">{{ m.body }}</div>
        </div>
      </div>
    </template>
  </div>
</div>`,
  setup(props) {
    const data = ref(null), loading = ref(false), error = ref(''), list = ref(null), n = ref(15);
    let target = null;
    async function load(scroll = true) {
      loading.value = !data.value || data.value.lookup_id !== props.lid;
      error.value = '';
      try {
        data.value = await api(`/api/context/${props.lid}?n=${n.value}`);
      } catch (e) { error.value = e.message; }
      loading.value = false;
      await nextTick();
      if (scroll && target) target.scrollIntoView({ block: 'center' });
    }
    watch(() => props.lid, () => { n.value = 15; load(); });
    onMounted(load);
    function more() { n.value = n.value < 30 ? 30 : 60; load(); }
    function showBody(m) {
      if (!m.media.length) return true;
      return !/^\[(图片|视频|语音|动画表情)\]$/.test(m.body.trim());
    }
    async function openArchive() {
      try { await api('/api/open', { method: 'POST', body: { lookup_id: props.lid } }); } catch (e) { fail(e); }
    }
    async function openMedia(x) {
      try { await api('/api/open', { method: 'POST', body: { media_id: x.id } }); } catch (e) { fail(e); }
    }
    return { data, loading, error, list, n, more, showBody, openArchive, openMedia, mediaUrl, ui, document,
             get target() { return target; }, set target(v) { target = v; } };
  },
};

function hashKey(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(36);
}

const md = window.markdownit({ html: false, linkify: true, breaks: false });
// CommonMark won't close **粗体。**正文 (punctuation inside, CJK letter outside); this relaxes the rule next to CJK.
md.use(window.markdownitCjkFriendly);
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  const t = tokens[idx], href = t.attrGet('href') || '', next = tokens[idx + 1];
  const m = next && next.type === 'text' && next.content.match(/^#(\d+)$/);
  if (m && /msg-[0-9a-f]+/.test(href)) {
    t.attrSet('href', '#');
    t.attrSet('class', 'ref');
    t.attrSet('data-lid', m[1]);
    t.attrSet('title', '查看原文');
  } else {
    t.attrSet('data-href', href);
  }
  return self.renderToken(tokens, idx, options);
};
md.core.ruler.push('heading_ids', state => {
  const toc = state.env.toc = [];
  state.tokens.forEach((t, i) => {
    if (t.type === 'heading_open') {
      const id = 'h' + toc.length;
      t.attrSet('id', id);
      toc.push({ id, level: +t.tag.slice(1), text: state.tokens[i + 1].content.replace(/\*\*/g, '') });
    }
  });
});

MRPages.reports = {
  components: { Icon, ContextView, MonthRun, SourceModal },
  template: `
<div class="page full">
  <div class="reader" :class="{noside: !side || tab !== 'result' || !report}">
    <div class="rlist">
      <div class="muted small" style="padding:4px 10px 8px">月报</div>
      <a v-for="m in list" :key="m.month" :href="'#/reports/' + m.month" :class="{on: m.month === month}">
        <span class="grow" style="min-width:0">
          <span style="display:block">{{ monthLabel(m.month) }}</span>
          <span class="muted small" style="display:block;font-weight:400" v-if="!m.report">{{ m.job === 'queued' ? '排队中' : m.job ? '运行中' : m.estimate ? '已估算，未生成' : '还没有生成' }}</span>
        </span>
        <span class="dot" :class="m.job ? 'run' : m.report ? (stateKind(m.state) === 'ok' && !m.stale.length ? 'ok' : 'warn') : ''"></span></a>
      <select class="input sm" style="margin:10px 4px 0;width:calc(100% - 8px)" v-if="others.length" @change="e => { const v = e.target.value; e.target.value = ''; if (v) go('reports', v, { tab: 'run' }); }">
        <option value="">生成其他月份…</option>
        <option v-for="m in others" :key="m" :value="m">{{ monthLabel(m) }}</option>
      </select>
      <div class="empty small" v-if="store.overview && !list.length && !others.length">归档里还没有完整导出的月份</div>
    </div>
    <div class="rmain">
      <template v-if="month">
      <div class="rtools">
        <b>{{ monthLabel(month) }}</b>
        <span class="chip" v-if="m && m.job" :class="m.job === 'queued' ? 'info' : 'accent'">{{ m.job === 'queued' ? '排队中' : m.job === 'plan' ? '估算中' : '运行中' }}</span>
        <span class="chip" v-else-if="m && m.state" :class="stateKind(m.state)">{{ stateText(m.state) }}</span>
        <span class="chip warn click" v-if="m && m.stale.length" role="button" title="看看变了什么，再决定要不要重新生成" @click="source = month">数据源有变化</span>
        <span class="muted small" v-if="report && report.status">{{ fmtTok(report.status.tokens_used) }} token</span>
        <span class="spacer"></span>
        <button class="btn sm" @click="estimate" :disabled="busy || !complete" title="只取数和分块，不调用模型"><Icon name="info"/>估算</button>
        <button class="btn sm primary" @click="start" :disabled="busy || !complete"><Icon name="play"/>{{ resumable ? '继续生成' : repairable ? '补做失败的部分' : m && m.report ? '重新生成' : '生成' }}</button>
        <button class="btn sm" @click="go('chats')" title="哪些会话会被读取；所有月份共用"><Icon name="chats"/>读取范围</button>
        <button class="btn ghost sm icon" v-if="m && (m.report || m.state || m.estimate) && !m.job" title="删除这期月报" @click="remove"><Icon name="trash"/></button>
      </div>
      <div class="tabs wtabs">
        <button :class="{on: tab === 'run'}" @click="setTab('run')">运行<span class="dot run" v-if="m && m.job" style="margin-left:6px"></span></button>
        <button :class="{on: tab === 'result'}" @click="setTab('result')">结果<span class="muted small" v-if="!hasReport">（还没有）</span></button>
        <span class="spacer"></span>
        <div class="row" style="gap:6px" v-if="tab === 'result' && report">
          <div class="find" v-if="finding">
            <input class="input sm" ref="findInput" v-model="findText" placeholder="查找" style="width:180px" @keydown.enter.exact.prevent="findNext(1)" @keydown.shift.enter.prevent="findNext(-1)" @keydown.esc="closeFind">
            <span class="small muted num" style="min-width:52px">{{ hits.length ? (hitIndex + 1) + ' / ' + hits.length : findText ? '无结果' : '' }}</span>
            <button class="btn ghost sm icon" @click="findNext(-1)" title="上一个（Shift+Enter）"><Icon name="up"/></button>
            <button class="btn ghost sm icon" @click="findNext(1)" title="下一个（Enter）"><Icon name="down"/></button>
            <button class="btn ghost sm icon" @click="closeFind" title="关闭（Esc）"><Icon name="x"/></button>
          </div>
          <button class="btn ghost sm icon" v-else title="查找（Ctrl+F）" @click="openFind"><Icon name="search"/></button>
          <button class="btn ghost sm" title="复制全文（Markdown）" @click="copy"><Icon name="copy"/>复制</button>
          <button class="btn ghost sm" title="用设置中的 Markdown 查看器打开；没有设置时用默认程序" @click="openPath(report.path)"><Icon name="ext"/>打开文件</button>
          <button class="btn ghost sm icon" title="在资源管理器中显示" @click="openPath(report.path, true)"><Icon name="folder"/></button>
          <button class="btn ghost sm icon" title="重新载入" @click="load"><Icon name="refresh"/></button>
          <button class="btn ghost sm icon" :title="side ? '隐藏侧栏' : '显示侧栏'" @click="side = !side"><Icon name="panel"/></button>
        </div>
      </div>
      <div class="rscroll" v-if="tab === 'run'"><div class="tpad"><MonthRun :month="month" :st="st"/></div></div>
      <div class="rscroll" v-show="tab === 'result'" ref="scroller" @scroll="onScroll">
        <div v-if="loading" class="empty"><span class="spinner"></span></div>
        <div v-else-if="error" class="empty">{{ error }}</div>
        <div v-else-if="report" class="md" ref="body" v-html="html" @click="onClick" @change="onCheck"></div>
        <div v-else class="empty">这个月还没有生成月报。<div style="margin-top:10px"><button class="btn" @click="setTab('run')">去运行</button></div></div>
      </div>
      </template>
      <div v-else class="empty">在左侧选择一个月份</div>
    </div>
    <div class="rside" v-if="side && tab === 'result' && report">
      <div class="tabs" style="margin:0;padding:0 8px">
        <button :class="{on: pane === 'toc'}" @click="pane = 'toc'">目录</button>
        <button :class="{on: pane === 'ctx'}" @click="pane = 'ctx'" title="点击月报里的 #编号，在这里查看那条消息和前后的聊天记录">聊天记录{{ lid ? ' #' + lid : '' }}</button>
      </div>
      <div class="toc" v-show="pane === 'toc'">
        <a v-for="t in toc" :key="t.id" :class="['l' + t.level, {on: t.id === activeId}]" href="" @click.prevent="jump(t.id)" :title="t.text">{{ t.text }}</a>
      </div>
      <ContextView v-if="pane === 'ctx' && lid" :lid="lid" @close="pane = 'toc'; lid = null"/>
      <div v-if="pane === 'ctx' && !lid" class="empty" style="text-align:left;line-height:1.8">
        点击月报里蓝色的 <span class="ref-sample">#编号</span>，这里会显示那条消息和前后的聊天记录，包括图片和语音。<br>
        还可以向前、向后展开更多消息，或者在 Markdown 查看器里打开整段原始记录。</div>
    </div>
  </div>
  <SourceModal v-if="source" :month="source" @close="source = null"/>
</div>`,
  setup() {
    const report = ref(null), loading = ref(false), error = ref(''), st = ref(null);
    const html = ref(''), toc = ref([]), body = ref(null), scroller = ref(null);
    const side = ref(loadLocal('report.side', true)), pane = ref('toc'), lid = ref(null), activeId = ref(null);
    const finding = ref(false), findText = ref(''), findInput = ref(null), hits = ref([]), hitIndex = ref(0);
    const months = computed(() => store.overview?.months || []);
    const byMonth = computed(() => Object.fromEntries(months.value.map(m => [m.month, m])));
    // Months that have something (newest first), and the other fully exported months to start one from.
    const list = computed(() => {
      const out = months.value.filter(m => m.report || m.state || m.job || m.estimate).reverse();
      const here = byMonth.value[route.arg];
      if (here && !out.includes(here)) out.unshift(here);
      return out;
    });
    const others = computed(() => months.value.filter(m => m.data === 'complete' && !list.value.includes(m)).map(m => m.month).reverse());
    const month = computed(() => route.arg || (list.value.find(m => m.report) || list.value[0])?.month || '');
    const m = computed(() => byMonth.value[month.value] || null);
    const complete = computed(() => m.value?.data === 'complete');
    const hasReport = computed(() => !!m.value?.report);
    const busy = computed(() => !!m.value?.job || runningJobs().some(j => j.month === month.value) || store.jobs.queue.some(j => j.month === month.value));
    const resumable = computed(() => monthResumable(st.value));
    const repairable = computed(() => monthRepairable(st.value));
    const tab = ref('result'), source = ref(null);
    function pickTab() { tab.value = ['run', 'result'].includes(route.query.tab) ? route.query.tab : hasReport.value ? 'result' : 'run'; }
    function setTab(t) {
      tab.value = t;
      // Also called when a job ends, which may be after another page was opened; that page stays.
      if (route.page === 'reports') history.replaceState(null, '', `#/reports/${month.value}${t === 'run' ? '?tab=run' : ''}`), parseRoute();
    }
    watch(side, v => saveLocal('report.side', v));
    async function loadStatus() {
      if (!month.value) return;
      const at = month.value;
      try {
        const [s, pl] = await Promise.all([api(`/api/months/${at}/status`), api(`/api/months/${at}/plan`)]);
        if (at === month.value) st.value = { ...s, plan: pl.plan };
      } catch (e) { fail(e); }
    }
    async function load() {
      report.value = null; html.value = ''; toc.value = []; error.value = '';
      if (!month.value || !hasReport.value) return;
      loading.value = true;
      try {
        const r = await api(`/api/reports/${month.value}`);
        const env = {};
        html.value = md.render(r.markdown, env);
        toc.value = env.toc || [];
        report.value = r;
      } catch (e) { error.value = e.message; report.value = null; }
      loading.value = false;
      await nextTick();
      decorate();
      const pos = loadLocal('report.pos.' + month.value, 0);
      if (scroller.value) scroller.value.scrollTop = pos;
      if (finding.value && findText.value) runFind();
    }
    async function estimate() { if (await addJobs([{ kind: 'plan', month: month.value }])) setTab('run'); }
    async function start() {
      const again = m.value && m.value.report && !m.value.stale.length && !resumable.value && !repairable.value;
      const recorded = st.value?.status?.agent === undefined ? null : st.value.status.agent ? 'agent' : 'api';
      const way = await chooseWay(again ? '重新生成' : resumable.value ? '继续生成' : repairable.value ? '补做失败的部分' : `生成 ${monthLabel(month.value)}`, {
        recorded, body: again ? `${monthLabel(month.value)} 已经有月报。重新生成会覆盖现有的月报文件；以前调用过、条件也没变的部分直接用缓存。` : '' });
      if (!way) return;
      const r = await addJobs([{ kind: 'run', month: month.value, agent: way === 'agent' }]);
      if (r && r.added.length) setTab('run');
    }
    async function remove() {
      if (!await deleteMonth(month.value, report.value?.status?.tokens_used ?? m.value?.tokens)) return;
      report.value = null; html.value = ''; toc.value = [];
      await loadOverview();
      go('reports', '');
    }
    // Task lines "[ ] …" become checkboxes whose state is kept on this computer.
    function decorate() {
      if (!body.value) return;
      const checks = report.value.checks || {};
      body.value.querySelectorAll('li').forEach(li => {
        const walker = document.createTreeWalker(li, NodeFilter.SHOW_TEXT);
        const first = walker.nextNode();
        if (!first) return;
        const m = first.nodeValue.match(/^\s*\[( |x|X)\]\s+/);
        if (!m) return;
        first.nodeValue = first.nodeValue.slice(m[0].length);
        const key = hashKey(li.textContent.trim().slice(0, 120));
        const on = key in checks ? checks[key] : m[1] !== ' ';
        const span = document.createElement('span');
        while (li.firstChild) span.appendChild(li.firstChild);
        const box = document.createElement('input');
        box.type = 'checkbox'; box.checked = on; box.dataset.key = key;
        li.append(box, span);
        li.classList.add('task');
        li.classList.toggle('checked', on);
      });
    }
    async function onCheck(ev) {
      const box = ev.target;
      if (box.type !== 'checkbox' || !box.dataset.key) return;
      box.closest('li').classList.toggle('checked', box.checked);
      try { report.value.checks = await api(`/api/reports/${month.value}/checks`, { method: 'PUT', body: { [box.dataset.key]: box.checked } }); }
      catch (e) { fail(e); }
    }
    function onClick(ev) {
      const a = ev.target.closest('a');
      if (!a) return;
      ev.preventDefault();
      if (a.classList.contains('ref')) {
        body.value.querySelectorAll('a.ref.on').forEach(x => x.classList.remove('on'));
        a.classList.add('on');
        lid.value = +a.dataset.lid; pane.value = 'ctx'; side.value = true;
        return;
      }
      const href = a.dataset.href || '';
      if (/^https?:\/\//i.test(href)) api('/api/open_url', { method: 'POST', body: { url: href } }).catch(fail);
      else if (href.startsWith('#')) { const el = body.value.querySelector(href); if (el) el.scrollIntoView(); }
      else if (href) {
        const base = report.value.path.replace(/[\\/][^\\/]*$/, '');
        openPath(base + '\\' + decodeURIComponent(href.replace(/^<|>$/g, '').split('#')[0]).replace(/\//g, '\\'));
      }
    }
    function jump(id) {
      const el = body.value && body.value.querySelector('#' + id);
      if (el) scroller.value.scrollTop = el.offsetTop - 12;
    }
    let scrollSave = null;
    function onScroll() {
      clearTimeout(scrollSave);
      scrollSave = setTimeout(() => {
        if (!scroller.value || !body.value) return;
        saveLocal('report.pos.' + month.value, scroller.value.scrollTop);
        const top = scroller.value.scrollTop + 40;
        let cur = null;
        for (const t of toc.value) { const el = body.value.querySelector('#' + t.id); if (el && el.offsetTop <= top) cur = t.id; else if (el) break; }
        activeId.value = cur;
      }, 120);
    }
    // In-page find: wrap matches in <mark>, step through them.
    function clearMarks() {
      if (!body.value) return;
      body.value.querySelectorAll('mark.hit').forEach(m => m.replaceWith(document.createTextNode(m.textContent)));
      body.value.normalize();
      hits.value = [];
    }
    function runFind() {
      clearMarks();
      const q = findText.value.trim().toLowerCase();
      if (!q || !body.value) return;
      const nodes = [];
      const walker = document.createTreeWalker(body.value, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) nodes.push(walker.currentNode);
      const found = [];
      for (const node of nodes) {
        const text = node.nodeValue, low = text.toLowerCase();
        let i = low.indexOf(q);
        if (i < 0) continue;
        const frag = document.createDocumentFragment();
        let last = 0;
        while (i >= 0) {
          frag.append(text.slice(last, i));
          const mark = document.createElement('mark');
          mark.className = 'hit'; mark.textContent = text.slice(i, i + q.length);
          frag.append(mark); found.push(mark);
          last = i + q.length; i = low.indexOf(q, last);
        }
        frag.append(text.slice(last));
        node.replaceWith(frag);
      }
      hits.value = found; hitIndex.value = 0;
      showHit();
    }
    function showHit() {
      hits.value.forEach((m, i) => m.classList.toggle('cur', i === hitIndex.value));
      const m = hits.value[hitIndex.value];
      if (m) m.scrollIntoView({ block: 'center' });
    }
    function findNext(d) {
      if (!hits.value.length) return runFind();
      hitIndex.value = (hitIndex.value + d + hits.value.length) % hits.value.length;
      showHit();
    }
    let findTimer = null;
    watch(findText, () => { clearTimeout(findTimer); findTimer = setTimeout(runFind, 200); });
    async function openFind() { finding.value = true; await nextTick(); findInput.value && findInput.value.focus(); findInput.value && findInput.value.select(); }
    function closeFind() { finding.value = false; findText.value = ''; clearMarks(); }
    function onKey(ev) {
      if (tab.value !== 'result' || !report.value) return;
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'f') { ev.preventDefault(); openFind(); }
      else if (ev.key === 'F3') { ev.preventDefault(); findNext(ev.shiftKey ? -1 : 1); }
    }
    async function copy() {
      try { await navigator.clipboard.writeText(report.value.markdown); toast('已复制全文（Markdown）', 'ok'); } catch (e) { fail(e); }
    }
    watch(month, async () => { lid.value = null; pane.value = 'toc'; st.value = null; pickTab(); await load(); loadStatus(); });
    watch(hasReport, (now, before) => { if (now !== before) load(); });
    // When this month's job ends, show its final state; a finished run opens the result.
    onJobEnd(rec => {
      if (rec.month !== month.value) return;
      loadStatus();
      if (rec.kind === 'run' && ['done', 'done-with-problems'].includes(rec.state)) loadOverview().then(() => { load(); setTab('result'); });
    });
    onMounted(async () => {
      window.addEventListener('keydown', onKey);
      if (!store.overview) await loadOverview();
      if (route.page === 'reports' && !route.arg && month.value) history.replaceState(null, '', '#/reports/' + month.value), parseRoute();
      pickTab(); load(); loadStatus();
    });
    onBeforeUnmount(() => window.removeEventListener('keydown', onKey));
    return { store, list, others, m, complete, hasReport, busy, resumable, repairable, st, report, loading, error, html, toc, body, scroller, side, tab, source, pane, lid, activeId, month,
             finding, findText, findInput, hits, hitIndex, findNext, openFind, closeFind, setTab, estimate, start, go,
             load, remove, onClick, onCheck, onScroll, jump, copy, openPath, monthLabel, stateText, stateKind, fmtTok };
  },
};
