/* The window: navigation, the running-job strip, and the pages. */
// Groups: the start page; the two report workspaces; jobs and voice that serve them; usage and settings.
const NAV = [[['overview', '总览']], [['reports', '月报'], ['topics', '专题']], [['run', '任务'], ['voice', '语音']], [['usage', '用量'], ['settings', '设置']]];
const NAV_PAGES = NAV.flat();

const App = {
  components: { Icon, Bar, ...Object.fromEntries(Object.entries(MRPages).map(([k, v]) => ['page-' + k, v])) },
  template: `
<div class="shell">
  <nav class="nav">
    <div class="brand"><img src="/ui/icon.svg" alt="">消息月报</div>
    <template v-for="(g, gi) in NAV" :key="gi">
      <div class="nav-sep" v-if="gi"></div>
      <a v-for="n in g" :key="n[0]" :href="'#/' + n[0]" :class="{on: navOn(n[0])}">
        <Icon :name="n[0]"/>{{ n[1] }}
        <span class="badge" v-if="n[0] === 'run' && cur"><span class="dot run"></span></span>
        <span class="badge chip" v-if="n[0] === 'run' && !cur && waiting">{{ waiting }}</span>
      </a>
    </template>
    <div class="foot">版本 {{ MR.version }}<br><span v-if="!store.connected" style="color:var(--bad)">本机服务未响应</span></div>
  </nav>
  <div class="topbar">
    <div class="runbar" v-if="cur" @click="goJob(cur)" title="查看运行详情">
      <span class="dot run"></span>
      <b>{{ curTitle }}</b>
      <template v-if="p">
        <span>{{ stepText }}</span>
        <Bar class="bar" :value="p.fraction"/>
        <span class="num">{{ Math.round((p.fraction || 0) * 100) }}%</span>
        <span class="muted" v-if="p.tokens">已用 {{ fmtTok(p.tokens) }} token</span>
        <span class="muted" v-if="p.eta_seconds">预计还要 {{ fmtDur(p.eta_seconds) }}</span>
      </template>
      <span v-else class="muted">{{ cur.kind === 'plan' || cur.kind === 'topic-plan' ? '正在取数（不调用模型）…' : '正在启动…' }}</span>
      <span class="spacer"></span>
      <span class="muted small" v-if="other">同时：{{ jobName(other) }}</span>
      <span class="muted small" v-if="waiting">之后还有 {{ waiting }} 个任务</span>
    </div>
  </div>
  <main class="main">
    <component :is="'page-' + page" :key="page === 'chats' ? 'chats/' + (route.arg || '') : page"/>
  </main>
</div>
<div class="toasts">
  <div v-for="t in ui.toasts" :key="t.id" class="toast" :class="t.kind"><div class="grow">{{ t.text }}</div>
    <button class="btn ghost sm icon" @click="ui.toasts.splice(ui.toasts.indexOf(t), 1)"><Icon name="x"/></button></div>
</div>
<div class="modal-back" v-if="ui.dialog" @mousedown.self="answer(false)">
  <div class="modal" style="width:min(480px,100%)" role="alertdialog" tabindex="-1" ref="dlg" @keydown.esc="answer(false)" @keydown.enter="answer(true)">
    <div class="modal-head"><h2>{{ ui.dialog.title }}</h2></div>
    <div class="modal-body"><div style="white-space:pre-wrap">{{ ui.dialog.body }}</div>
      <label v-if="ui.dialog.check" class="row" style="margin-top:12px;cursor:pointer"><input type="checkbox" v-model="ui.dialog.checked">{{ ui.dialog.check }}</label>
      <div v-if="ui.dialog.choices" class="col" style="gap:8px;margin-top:12px;align-items:stretch">
        <label v-for="c in ui.dialog.choices" :key="c.value" class="row"
               :style="{ alignItems: 'flex-start', gap: '10px', cursor: 'pointer', padding: '10px 12px', borderRadius: 'var(--radius)',
                         border: '1px solid ' + (ui.dialog.choice === c.value ? 'var(--accent)' : 'var(--line)'),
                         background: ui.dialog.choice === c.value ? 'var(--accent-bg)' : 'transparent' }">
          <input type="radio" :value="c.value" v-model="ui.dialog.choice" style="margin-top:3px">
          <div class="grow"><b>{{ c.label }}</b><div class="muted small" style="margin-top:2px">{{ c.hint }}</div></div>
        </label>
      </div>
      <div v-if="ui.dialog.type" style="margin-top:12px">
        <div class="small" style="margin-bottom:6px">请输入"{{ ui.dialog.type }}"确认：</div>
        <input class="input" style="width:100%" v-model="ui.dialog.typed" @keydown.enter.stop="answer(true)">
      </div></div>
    <div class="modal-foot"><button class="btn" @click="answer(false)">{{ ui.dialog.cancel }}</button>
      <button class="btn" :class="ui.dialog.danger ? 'danger solid' : 'primary'" :disabled="!allowed()" @click="answer(true)">{{ ui.dialog.ok }}</button></div>
  </div>
</div>
<div class="lightbox" v-if="ui.lightbox" @click="ui.lightbox = null"><img :src="ui.lightbox" alt=""></div>`,
  setup() {
    const page = computed(() => MRPages[route.page] ? route.page : 'overview');
    // 读取范围 belongs to the workspace it was opened from.
    const navOn = k => route.page === k || (route.page === 'chats' && k === (route.arg ? 'topics' : 'reports'));
    // The bar shows the job calling models; an estimate beside it is named there, or shown when it runs alone.
    const cur = computed(() => store.jobs.current || store.jobs.side);
    const other = computed(() => store.jobs.current && store.jobs.side);
    const waiting = computed(() => store.jobs.queue.filter(j => !j.paused).length);     // paused ones start only when resumed
    const p = computed(() => cur.value?.progress);
    const curTitle = computed(() => !cur.value ? '' : jobName(cur.value));
    const stepText = computed(() => {
      const s = (p.value?.steps || []).find(x => x.key === p.value.step);
      return s ? `${s.label}${s.total ? ' ' + s.done + '/' + s.total : ''}` : p.value?.message || '';
    });
    const dlg = ref(null);
    watch(() => ui.dialog, async d => { if (d) { await nextTick(); dlg.value && dlg.value.focus(); } });
    const allowed = () => !ui.dialog || !ui.dialog.type || ui.dialog.typed.trim() === ui.dialog.type;
    function answer(v) {
      if (v && !allowed()) return;          // the text has not been typed: neither the button nor Enter goes through
      const d = ui.dialog; ui.dialog = null; d && d.resolve(v && (d.check || d.choices) ? { checked: d.checked, choice: d.choice } : v);
    }
    return { NAV, MR, route, store, ui, page, navOn, cur, other, waiting, p, curTitle, stepText, dlg, answer, allowed, go, goJob, jobName, fmtTok, fmtDur };
  },
};

let lastHash = location.hash;
// The page a hash shows; each 读取范围 belongs to its owner, so another owner's is another page.
const pageOf = h => { const [, p, a] = h.replace(/\?.*/, '').split('/'); return p === 'chats' ? 'chats/' + (a || '') : p; };
window.addEventListener('hashchange', async () => {
  const msg = leaveGuards.map(g => g()).find(Boolean);
  const target = location.hash;
  const samePage = pageOf(target) === pageOf(lastHash);
  if (msg && !samePage) {
    history.replaceState(null, '', lastHash);
    if (!await confirmBox('离开这一页', msg, { ok: '离开', danger: true })) return;
    leaveGuards.length = 0;
    history.replaceState(null, '', target);
  }
  lastHash = target;
  parseRoute();
});
// Only one recording plays at a time, on any page: starting one pauses the others.
document.addEventListener('play', e => {
  if (e.target instanceof HTMLMediaElement) for (const m of document.querySelectorAll('audio, video')) if (m !== e.target) m.pause();
}, true);
window.addEventListener('beforeunload', e => { if (leaveGuards.some(g => g())) { e.preventDefault(); e.returnValue = ''; } });
window.addEventListener('keydown', e => {
  // Ctrl+1…7 switch pages, like tabs.
  if ((e.ctrlKey || e.metaKey) && /^[1-7]$/.test(e.key)) { e.preventDefault(); go(NAV_PAGES[+e.key - 1][0]); }
});

parseRoute();
const app = Vue.createApp(App);
app.config.errorHandler = (err) => { console.error(err); toast('界面出错：' + (err.message || err), 'bad', 10000); };
app.component('Icon', Icon);
app.mount('#app');
pollJobs();
loadOverview();
loadSettings().catch(() => {});
