/* Shared pieces: API calls, the polled job state, formatting, icons and small components. */
const { reactive, ref, computed, watch, onMounted, onBeforeUnmount, nextTick, h } = Vue;

const MRPages = {};

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: { 'X-MR-Token': MR.token } };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  let r;
  try {
    r = await fetch(path, init);
  } catch (e) {
    throw new Error('连接不到本机服务。请关闭窗口后重新打开消息月报。');
  }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text.slice(0, 300) }; }
  if (!r.ok) {
    const err = new Error((data && (data.error || data.detail)) || `请求失败（${r.status}）`);
    err.status = r.status;
    throw err;
  }
  return data;
}
const mediaUrl = (id, thumb) => `/api/media/${encodeURIComponent(id)}?t=${encodeURIComponent(MR.token)}${thumb ? '&thumb=1' : ''}`;

/* ---------- toasts and dialogs ---------- */
const ui = reactive({ toasts: [], dialog: null, lightbox: null });
let toastId = 0;
function toast(text, kind = 'info', ms = 4200) {
  const id = ++toastId;
  ui.toasts.push({ id, text, kind });
  if (ms) setTimeout(() => { const i = ui.toasts.findIndex(t => t.id === id); if (i >= 0) ui.toasts.splice(i, 1); }, ms);
}
function fail(e) { toast(e.message || String(e), 'bad', 8000); }
// With a check label the dialog also shows a checkbox and resolves to false or { checked }.
// With type, the dialog asks for that text to be typed and only then lets the action through.
// choices: [{ value, label, hint }] shown as options, choice the one picked first; the answer is then { choice }.
function confirmBox(title, body, { ok = '确定', danger = false, cancel = '取消', check = null, type = null, choices = null, choice = null } = {}) {
  return new Promise(resolve => { ui.dialog = { title, body, ok, cancel, danger, check, checked: false, type, typed: '', choices, choice, resolve }; });
}

// Who does the stages ticked under 设置 › 外部 Agent this time: 'api' (their models) or 'agent'; null when the dialog is
// closed. The question joins the confirmation a start already asks for (body); with no stage ticked there it is not
// asked: the models, after that confirmation if there is one. recorded: how this month or 专题 was started last time.
async function chooseWay(title, { recorded = null, where = '这个月份的"运行"里', body = '', ok = '开始' } = {}) {
  if (!store.settings) { try { await loadSettings(); } catch (e) { fail(e); return null; } }
  const ticked = store.settings.settings.external_stages || [];
  if (!ticked.length) return !body || await confirmBox(title, body, { ok }) ? 'api' : null;
  const names = (store.settings.external.stages || []).filter(s => ticked.includes(s.key)).map(s => s.label).join('、');
  const r = await confirmBox(title, (body ? body + '\n\n' : '') + `${names}这次由谁来做？其余环节都用模型。`, { ok, choice: recorded || loadLocal('way', 'api'), choices: [
    { value: 'api', label: '模型', hint: '通过接口调用，按接口计费。和其他任务排队，一个接一个进行。' },
    { value: 'agent', label: '外部 Agent', hint: `任务马上写出，不用等前面的任务：在${where}复制提示词发给 Agent。其余环节排队，轮到时进行。` }] });
  if (!r) return null;
  saveLocal('way', r.choice);
  return r.choice;
}

// Delete a month's report: files go to the Recycle Bin, and the month leaves the usage page.
async function deleteMonth(month, tokens) {
  const r = await confirmBox(`删除 ${monthLabel(month)} 的月报`,
    '月报、结论卡、进度、运行日志和用量记录会移到回收站，可以从回收站恢复。用量页不再显示这个月。\n\n保留缓存时，以后重新生成这个月，以前调用过、条件也没变的部分直接用缓存，这部分的用量会重新算进新的月报。',
    { ok: '删除', danger: true, check: '同时删除缓存（以后重新生成要重新调用模型' + (tokens ? `，约 ${fmtTok(tokens)} token` : '') + '）' });
  if (!r) return false;
  try {
    await api(`/api/months/${month}?cache=${r.checked ? 1 : 0}`, { method: 'DELETE' });
    toast(`${monthLabel(month)} 的月报已删除，文件在回收站`, 'ok');
    loadOverview();
    return true;
  } catch (e) { fail(e); return false; }
}

/* ---------- formatting ---------- */
function fmtTok(n) {
  if (n === null || n === undefined) return '—';
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(n >= 1e8 ? 0 : 2).replace(/\.?0+$/, '') + 'M';
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1).replace(/\.0$/, '') + 'K';
  return String(Math.round(n));
}
const fmtInt = n => (n === null || n === undefined) ? '—' : Math.round(n).toLocaleString('zh-CN');
function fmtDur(s) {
  if (s === null || s === undefined || isNaN(s)) return '—';
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s} 秒`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} 分钟`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}
function fmtTime(ts, withSec) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}${withSec ? ':' + p(d.getSeconds()) : ''}`;
}
const fmtDate = ts => ts ? fmtTime(ts).slice(0, 10) : '—';
function fmtMoney(v, cur) {
  if (v === null || v === undefined) return '未定价';
  const sym = { USD: '$', CNY: '¥', EUR: '€' }[cur] || (cur ? cur + ' ' : '');
  return sym + (v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(2) : v.toFixed(4));
}
const pct = (a, b) => b ? Math.round(a / b * 1000) / 10 + '%' : '—';
// Chat categories are archive folders at any depth; a rule on a folder covers everything below it.
const catAncestors = c => c.split('/').map((_, i, a) => a.slice(0, i + 1).join('/'));
const catWithin = (c, k) => c === k || c.startsWith(k + '/');
const catPart = p => p.replace(/^\d+_/, '');
const catTitle = c => c.split('/').map(catPart).join(' · ');
function monthLabel(m) { return !m ? '' : /^\d{4}-\d{2}$/.test(m) ? `${m.slice(0, 4)} 年 ${+m.slice(5)} 月` : m; }

const STATE = {
  done: ['完成', 'ok'], 'done-with-problems': ['部分失败', 'warn'], 'budget-stop': ['预算暂停', 'warn'],
  'external-wait': ['等待外部 Agent', 'info'],
  failed: ['失败', 'bad'], stopped: ['已停止', 'warn'], paused: ['已暂停', 'warn'], running: ['运行中', 'accent'], 'read-done': ['已读完', 'info'],
  missing: ['月报文件不在了', 'warn'],
};
const stateText = s => (STATE[s] || [s || '未生成'])[0];
const stateKind = s => (STATE[s] || [null, ''])[1];

/* ---------- icons (inline, stroke) ---------- */
const ICONS = {
  overview: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  run: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 5.5l1 1 2-2M4 11.5l1 1 2-2"/><circle cx="5" cy="18" r="1.2"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  reports: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  chats: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
  voice: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  usage: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  topics: '<circle cx="11" cy="11" r="6.5"/><path d="M20.5 20.5l-4.9-4.9M8.5 11h5M11 8.5v5"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  pause: '<rect x="7" y="5" width="3.5" height="14" rx="1"/><rect x="13.5" y="5" width="3.5" height="14" rx="1"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  up: '<path d="M12 19V5M6 11l6-6 6 6"/>', down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>', search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  folder: '<path d="M3 6h6l2 2h10v11H3z"/>', download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>', ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>', refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>', trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  check: '<path d="M5 12l5 5 9-10"/>', warn: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>', log: '<path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>', redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
};
const Icon = {
  props: { name: String, size: { type: Number, default: 16 } },
  setup(props) {
    return () => h('svg', { viewBox: '0 0 24 24', width: props.size, height: props.size, fill: 'none', stroke: 'currentColor',
      'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', innerHTML: ICONS[props.name] || '' });
  },
};

const Bar = {
  props: { value: Number, kind: String, big: Boolean, indet: Boolean },
  template: `<div class="bar" :class="[kind, {big, indet}]"><i :style="{width: Math.max(0, Math.min(100, (value || 0) * 100)) + '%'}"></i></div>`,
};

const Modal = {
  props: { title: String, wide: Boolean },
  emits: ['close'],
  template: `<div class="modal-back" @mousedown.self="$emit('close')" @keydown.esc="$emit('close')">
    <div class="modal" :class="{wide}" role="dialog" tabindex="-1" ref="box">
      <div class="modal-head"><h2>{{ title }}</h2><span class="spacer"></span>
        <button class="btn ghost icon sm" title="关闭" @click="$emit('close')"><Icon name="x"/></button></div>
      <div class="modal-body"><slot/></div>
      <div class="modal-foot" v-if="$slots.foot"><slot name="foot"/></div>
    </div></div>`,
  components: { Icon },
  mounted() { this.$refs.box.focus(); },
};

// What changed in the archive for a month since its report was written, and the choice between writing it again and keeping it.
const SourceModal = {
  components: { Icon, Modal },
  props: { month: String },
  emits: ['close'],
  template: `
<Modal :title="monthLabel(month) + ' · 数据源有变化'" wide @close="$emit('close')">
  <div v-if="!d" class="muted">正在比较…</div>
  <template v-else>
    <div style="margin-bottom:10px">这期月报生成时，归档里这个月有 <b>{{ fmtInt(d.then) }}</b> 条消息；现在是 <b>{{ fmtInt(d.now) }}</b> 条，{{ d.now >= d.then ? '多了' : '少了' }} <b>{{ fmtInt(Math.abs(d.now - d.then)) }}</b> 条（{{ pct(Math.abs(d.now - d.then), d.then) }}）。</div>
    <template v-if="d.chats">
      <div class="muted small" style="margin-bottom:8px">条数变了的会话 {{ d.chats.length }} 个。会被读取的 {{ read.length }} 个，合计变化 {{ fmtInt(readDelta) }} 条；其余 {{ d.chats.length - read.length }} 个不读取，对月报没有影响。</div>
      <div style="max-height:52vh;overflow:auto" v-if="rows.length">
        <table class="t small">
          <thead><tr><th>会话</th><th>分类</th><th class="r" style="width:90px">生成时</th><th class="r" style="width:90px">现在</th><th class="r" style="width:90px">变化</th><th style="width:70px"></th></tr></thead>
          <tbody><tr v-for="(c, i) in rows" :key="i" :class="{ muted: !c.read }">
            <td>{{ c.name }}</td><td class="muted">{{ catTitle(c.category) }}</td>
            <td class="r num">{{ fmtInt(c.then) }}</td><td class="r num">{{ fmtInt(c.now) }}</td>
            <td class="r num"><b>{{ c.now > c.then ? '+' : '−' }}{{ fmtInt(Math.abs(c.now - c.then)) }}</b></td>
            <td><span class="chip" :class="c.read ? 'info' : ''">{{ c.read ? '读取' : '不读取' }}</span></td>
          </tr></tbody>
        </table>
      </div>
    </template>
    <template v-else>
      <div style="margin-bottom:10px" v-if="d.reading">读取范围内：月报读了 <b>{{ fmtInt(d.reading.then) }}</b> 条，现在会读 <b>{{ fmtInt(d.reading.now) }}</b> 条（{{ signed(d.reading.now - d.reading.then) }}）。不读取的会话：{{ fmtInt(d.skipped.then) }} 条变为 {{ fmtInt(d.skipped.now) }} 条（{{ signed(d.skipped.now - d.skipped.then) }}），对月报没有影响。</div>
      <div class="muted small" style="margin-bottom:8px">这期月报生成时还没有记下每个会话的条数，下面按分类比较月报附录里写的读取量和现在会读的量<template v-if="!d.categories">（这期月报的附录里没有这张表，无法比较）</template>。</div>
      <div style="max-height:44vh;overflow:auto" v-if="d.categories && d.categories.length">
        <table class="t small">
          <thead><tr><th>分类</th><th class="r" style="width:150px">月报读了</th><th class="r" style="width:150px">现在会读</th><th class="r" style="width:100px">消息变化</th></tr></thead>
          <tbody><tr v-for="c in d.categories" :key="c.category">
            <td>{{ catTitle(c.category) }}</td>
            <td class="r num">{{ c.then_chats }} 个会话 / {{ fmtInt(c.then) }} 条</td><td class="r num">{{ c.now_chats }} 个会话 / {{ fmtInt(c.now) }} 条</td>
            <td class="r num"><b>{{ signed(c.now - c.then) }}</b></td>
          </tr></tbody>
        </table>
      </div>
      <div class="muted small" v-else-if="d.categories">各分类的读取量和月报生成时一样。</div>
      <div class="small" style="margin-top:10px" v-if="rows.length">生成之后被重新写过的会话 {{ rows.length }} 个（会被读取的 {{ read.length }} 个）：{{ rows.slice(0, 12).map(c => c.name).join('、') }}<template v-if="rows.length > 12"> 等</template>。</div>
    </template>
  </template>
  <template #foot>
    <span class="muted small">保留以后，以现在的归档为准；之后再有变化会重新提醒，并列出每个会话变了多少。</span><span class="spacer"></span>
    <button class="btn" :disabled="!d || busy" @click="keep">保留现有月报，不再提醒</button>
    <button class="btn primary" :disabled="busy" @click="again"><Icon name="play"/>重新生成</button>
  </template>
</Modal>`,
  setup(props, { emit }) {
    const d = ref(null), busy = ref(false);
    // Chats a run reads come first: only they can change the report.
    const rows = computed(() => [...(d.value?.chats || d.value?.rewritten || [])].sort((a, b) => b.read - a.read));
    const read = computed(() => rows.value.filter(c => c.read));
    const readDelta = computed(() => read.value.reduce((s, c) => s + Math.abs(c.now - c.then), 0));
    const signed = n => (n > 0 ? '+' : n < 0 ? '−' : '') + fmtInt(Math.abs(n));
    onMounted(async () => { try { d.value = await api(`/api/months/${props.month}/source`); } catch (e) { fail(e); emit('close'); } });
    async function keep() {
      busy.value = true;
      try { await api(`/api/months/${props.month}/source/accept`, { method: 'POST', body: {} }); toast('已保留现有月报，提醒已去掉', 'ok'); await loadOverview(); emit('close'); }
      catch (e) { fail(e); busy.value = false; }
    }
    async function again() {
      const way = await chooseWay('重新生成');
      if (!way) return;
      const r = await addJobs([{ kind: 'run', month: props.month, agent: way === 'agent' }]);
      if (r && r.added.length) go('reports', props.month, { tab: 'run' });
      emit('close');
    }
    return { d, busy, rows, read, readDelta, signed, keep, again, fmtInt, pct, monthLabel, catTitle };
  },
};

/* ---------- shared state, polled ---------- */
const store = reactive({
  jobs: { queue: [], history: [], current: null }, overview: null, overviewError: null, overviewReading: null, settings: null, models: null, connected: true,
  speed: [],            // recent [time, tokens] samples of the running job, for tokens per minute
});
let pollTimer = null;
// The jobs running now: the one calling models, and an estimate beside it.
const runningJobs = (jobs = store.jobs) => [jobs.current, jobs.side].filter(Boolean);
// fn(record) when a running job ends, with its record from the history.
function onJobEnd(fn) {
  return watch(() => runningJobs().map(j => j.id).join('|'), (now, before) => {
    const still = now.split('|');
    for (const id of (before || '').split('|')) {
      const rec = id && !still.includes(id) && store.jobs.history.find(r => r.id === id);
      if (rec) fn(rec);
    }
  });
}
async function pollJobs() {
  try {
    const before = runningJobs();
    const data = await api('/api/jobs');
    store.connected = true;
    const cur = data.current;
    if (cur && cur.progress) {
      const now = Date.now() / 1000;
      store.speed.push([now, cur.progress.tokens || 0]);
      while (store.speed.length > 2 && now - store.speed[0][0] > 180) store.speed.shift();
    } else store.speed = [];
    // A job that just ended: say how it went and refresh what depends on it.
    const now = runningJobs(data).map(j => j.id);
    const ended = before.filter(j => !now.includes(j.id));
    for (const j of ended) {
      const rec = data.history.find(r => r.id === j.id);
      if (rec) toast(`${jobName(rec)}：${stateText(rec.state)}${rec.message ? '\n' + rec.message : ''}`, stateKind(rec.state) === 'ok' ? 'ok' : stateKind(rec.state) || 'info', 9000);
    }
    if (ended.length) loadOverview();
    store.jobs = data;
  } catch (e) {
    store.connected = false;
  }
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollJobs, runningJobs().length ? 1200 : 3000);
}
let readingTimer = null;
async function loadOverview() {
  // The overview page shows why it could not load (often: no archive folder chosen yet), so no toast here.
  // 503: the archive is still being read the first time; show how far it has got and ask again shortly.
  clearTimeout(readingTimer);
  try {
    store.overview = await api('/api/overview'); store.overviewError = null; store.overviewReading = null;
  } catch (e) {
    if (e.status === 503) { store.overviewReading = e.message; store.overviewError = null; readingTimer = setTimeout(loadOverview, 2000); }
    else { store.overviewError = e.message; store.overviewReading = null; }
  }
}
async function loadSettings() { const r = await api('/api/settings'); store.settings = r; return r; }
async function loadModels() { const r = await api('/api/models'); store.models = r; return r; }
function tokensPerMinute() {
  const s = store.speed;
  if (s.length < 2) return null;
  const [t0, k0] = s[0], [t1, k1] = s[s.length - 1];
  return t1 - t0 > 5 ? (k1 - k0) / (t1 - t0) * 60 : null;
}
function jobName(j) {
  if (j.kind === 'asr') return '语音转写';
  if (j.kind.startsWith('topic-')) return `专题「${j.title || '未命名'}」${{ 'topic-plan': '预览', 'topic-screen': '筛查', 'topic-run': '' }[j.kind]}`;
  return `${j.month} ${j.kind === 'plan' ? '估算' : '月报'}`;
}
async function addJobs(jobs) {
  try {
    const r = await api('/api/jobs', { method: 'POST', body: { jobs } });
    if (!r.added.length) toast('这些任务已经在队列中', 'warn');
    pollJobs();
    return r;
  } catch (e) { fail(e); }
}
async function openPath(path, reveal) {
  try { await api('/api/open', { method: 'POST', body: { path, reveal } }); } catch (e) { fail(e); }
}

/* hash routes: #/page/arg?query */
const route = reactive({ page: 'overview', arg: '', query: {} });
function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, qs] = raw.split('?');
  const parts = path.split('/').filter(Boolean);
  route.page = parts[0] || 'overview';
  route.arg = decodeURIComponent(parts.slice(1).join('/') || '');
  route.query = Object.fromEntries(new URLSearchParams(qs || ''));
}
function go(page, arg, query) {
  const qs = query ? '?' + new URLSearchParams(query).toString() : '';
  location.hash = `#/${page}${arg ? '/' + encodeURIComponent(arg) : ''}${qs}`;
}
const leaveGuards = [];       // functions returning a message when leaving would lose work

function saveLocal(key, value) { try { localStorage.setItem('mr.' + key, JSON.stringify(value)); } catch (e) {} }
function loadLocal(key, fallback) {
  try { const v = localStorage.getItem('mr.' + key); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
}
function lastCompleteMonth() {
  const o = store.overview;
  if (!o) return null;
  const done = o.months.filter(m => m.data === 'complete');
  return done.length ? done[done.length - 1].month : null;
}
function shiftMonth(m, delta) {
  let y = +m.slice(0, 4), mo = +m.slice(5) + delta;
  while (mo < 1) { mo += 12; y--; }
  while (mo > 12) { mo -= 12; y++; }
  return `${y}-${String(mo).padStart(2, '0')}`;
}
