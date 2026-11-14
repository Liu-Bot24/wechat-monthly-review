/* 语音识别: batch transcription ahead of reports, and browsing what was transcribed. */
MRPages.voice = {
  components: { Icon, Bar },
  template: `
<div class="page">
  <div class="page-head">
    <h1>语音识别</h1>
    <span class="sub">读取之前先把语音转成文字；生成月报时直接使用这里的结果。</span>
  </div>
  <div class="banner" :class="svc ? (svc.ok ? 'ok' : 'bad') : 'info'">
    <span class="dot" :class="svc ? (svc.ok ? 'ok' : 'bad') : 'run'" style="margin-top:6px"></span>
    <div class="grow" v-if="!svc">正在检查语音识别服务…</div>
    <div class="grow" v-else-if="svc.ok">语音识别服务正常：{{ svc.model }}（{{ svc.base_url }}，响应 {{ svc.ms }} 毫秒）</div>
    <div class="grow" v-else>{{ svc.message }}<template v-if="svc.state === 'down'">
启动语音识别服务后点"重新检查"；地址不对时，在"更换识别接口"中修改。</template></div>
    <button class="btn sm" @click="checkService">重新检查</button>
    <a class="btn sm" href="#/settings/models">更换识别接口</a>
  </div>
  <div class="card pad" style="margin-bottom:14px">
    <div class="row wrap">
      <span>范围</span>
      <input class="input" type="month" v-model="first" style="width:150px"> <span>至</span>
      <input class="input" type="month" v-model="last" style="width:150px">
      <select class="input" v-model="chat" style="width:240px">
        <option value="">全部要读取的会话</option>
        <option v-for="c in chatOptions" :key="c.chat" :value="c.chat">{{ c.name }}（{{ c.n }} 条）</option>
      </select>
      <span class="spacer"></span>
      <button class="btn" @click="load()"><Icon name="refresh"/>刷新</button>
      <button class="btn danger" v-if="running" @click="stop"><Icon name="stop"/>停止</button>
      <button class="btn primary" v-else :disabled="!data || !data.pending || busy" @click="start"><Icon name="play"/>转写待处理的 {{ data ? data.pending : '' }} 条</button>
    </div>
    <div class="muted small" style="margin-top:8px">只包括月报读取范围中会读取的会话；选择某个会话时，不论它是否读取都会转写。</div>
  </div>
  <div class="tiles" v-if="data">
    <div class="tile"><div class="k">语音</div><div class="v">{{ fmtInt(data.total) }}</div></div>
    <div class="tile"><div class="k">已转写</div><div class="v">{{ fmtInt(data.done) }}</div><div class="d">{{ pct(data.done, data.total) }}</div></div>
    <div class="tile"><div class="k">待转写</div><div class="v">{{ fmtInt(data.pending) }}</div><div class="d" v-if="data.pending">预计 {{ fmtDur(data.eta_seconds) }}（每条约 {{ data.pace.toFixed(1) }} 秒）</div></div>
  </div>
  <div class="card" style="margin-bottom:14px" v-if="prog">
    <div class="card-head"><span class="dot" :class="running ? 'run' : stateKind(prog.state)"></span><h2>{{ running ? '正在转写' : '上次转写' }}：{{ prog.month }}</h2>
      <span class="chip" v-if="!running" :class="stateKind(prog.state)">{{ stateText(prog.state) }}</span></div>
    <div class="card-body">
      <div class="row small" style="margin-bottom:6px"><b>{{ prog.message }}</b><span class="spacer"></span>
        <span class="muted" v-if="running && prog.eta_seconds">预计还要 {{ fmtDur(prog.eta_seconds) }}</span></div>
      <Bar :value="prog.fraction" big/>
      <div class="kv" style="margin-top:10px" v-if="prog.steps[0]">
        <div><div class="k">完成</div><div class="v">{{ prog.steps[0].done }} / {{ prog.steps[0].total }}</div></div>
        <div><div class="k">失败</div><div class="v" :style="{color: prog.steps[0].failed ? 'var(--bad)' : ''}">{{ prog.steps[0].failed }}</div></div>
      </div>
      <div v-if="failures.length" style="margin-top:10px">
        <div class="small" style="font-weight:600;margin-bottom:4px">失败（重新开始转写时会自动重试）</div>
        <div class="events"><div v-for="(e, i) in failures" :key="i"><span>{{ fmtTime(e.at).slice(11) }}</span><span>{{ e.text }}</span></div></div>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><h2>转写结果</h2>
      <div class="seg sm"><button v-for="s in [['', '全部'], ['done', '已转写'], ['pending', '待转写']]" :key="s[0]" :class="{on: state === s[0]}" @click="state = s[0]">{{ s[1] }}</button></div>
      <input class="input sm" v-model="filter" placeholder="筛选文字" style="width:180px">
      <span class="spacer"></span><span class="muted small" v-if="data">{{ fmtInt(data.matched) }} 条</span></div>
    <div v-if="loading" class="empty"><span class="spinner"></span></div>
    <table class="t" v-else-if="shown.length">
      <thead><tr><th style="width:130px">时间</th><th style="width:200px">会话</th><th style="width:120px">谁说的</th><th style="width:240px">原音</th><th>文字</th></tr></thead>
      <tbody><tr v-for="it in shown" :key="it.id">
        <td class="num small">{{ it.time }}</td><td class="ellipsis" style="max-width:200px" :title="it.name">{{ it.name }}</td><td class="small">{{ it.sender }}</td>
        <td><audio v-if="playing.has(it.id)" controls autoplay preload="none" :src="mediaUrl(it.id)" style="height:30px;width:230px"></audio>
          <button v-else class="btn sm" @click="play(it.id)"><Icon name="play"/>播放</button></td>
        <td><span v-if="it.text !== null">{{ it.text || '（没有识别出内容）' }}</span><span v-else class="muted">待转写</span></td>
      </tr></tbody>
    </table>
    <div class="empty" v-else>这个范围内没有语音</div>
    <div class="row" style="justify-content:center;padding:10px" v-if="data && data.items.length < data.matched"><button class="btn" @click="more">再显示 200 条</button></div>
  </div>
</div>`,
  setup() {
    const lastM = lastCompleteMonth() || new Date().toISOString().slice(0, 7);
    const first = ref(loadLocal('voice.first', lastM)), last = ref(loadLocal('voice.last', lastM));
    const chat = ref(''), state = ref(''), filter = ref('');
    const data = ref(null), loading = ref(false), svc = ref(null), playing = ref(new Set()), lastProg = ref(null);
    const chatOptions = ref([]);
    const cur = computed(() => store.jobs.current);
    const running = computed(() => cur.value && cur.value.kind === 'asr');
    const busy = computed(() => !!cur.value || store.jobs.queue.some(j => j.kind === 'asr'));
    const prog = computed(() => running.value ? cur.value.progress : lastProg.value);
    const failures = computed(() => (prog.value?.events || []).filter(e => e.text.startsWith('转写失败')).reverse());
    const shown = computed(() => {
      const items = data.value?.items || [];
      const s = filter.value.trim();
      return s ? items.filter(i => (i.text || '').includes(s) || i.name.includes(s)) : items;
    });
    // Each answer is shown only if it is still the newest asked for, so a slow one for an earlier range never replaces a
    // later one. The spinner shows only while a different range or filter is loading; the refresh during a transcription
    // updates the rows in place, keeping as many as are already shown.
    let seq = 0, refreshing = false;
    const query = () => [first.value, last.value, chat.value, state.value].join('|');
    async function load(append) {
      if (first.value > last.value) return toast('起始月份不能晚于结束月份', 'warn');
      saveLocal('voice.first', first.value); saveLocal('voice.last', last.value);
      const key = query(), mine = ++seq;
      const same = data.value && data.value.key === key;
      if (!same) loading.value = true;
      const offset = append && same ? data.value.items.length : 0;
      const limit = !append && same ? Math.max(200, data.value.items.length) : 200;
      try {
        const qs = new URLSearchParams({ first: first.value, last: last.value, chat: chat.value, state: state.value, offset, limit });
        const r = await api('/api/voice?' + qs);
        if (mine !== seq) return;
        if (offset) r.items = data.value.items.concat(r.items);
        data.value = { ...r, key };
        if (!chat.value) chatOptions.value = r.chats;
      } catch (e) { if (mine === seq) fail(e); }
      if (mine === seq) loading.value = false;
    }
    async function refresh() {
      if (refreshing || loading.value) return;
      refreshing = true;
      try { await load(); } finally { refreshing = false; }
    }
    async function checkService() { svc.value = null; try { svc.value = await api('/api/voice/service'); } catch (e) { svc.value = { ok: false, message: e.message }; } }
    async function loadLastProgress() {
      try { lastProg.value = (await api('/api/voice/progress')).progress; } catch (e) {}
    }
    function more() { load(true); }
    function play(id) { const s = new Set(playing.value); s.add(id); playing.value = s; }
    async function start() {
      if (svc.value && !svc.value.ok) {
        const ok = await confirmBox('语音识别服务不可用', svc.value.message + '\n仍然加入队列吗？');
        if (!ok) return;
      }
      await addJobs([{ kind: 'asr', from: first.value, to: last.value, chats: chat.value ? [chat.value] : null }]);
    }
    async function stop() { try { store.jobs = await api('/api/jobs/stop', { method: 'POST', body: {} }); } catch (e) { fail(e); } }
    watch([first, last, chat, state], () => load());
    watch(running, (now, before) => { if (before && !now) { load(); loadLastProgress(); } });
    let refresher = setInterval(() => { if (running.value) refresh(); }, 15000);
    onBeforeUnmount(() => clearInterval(refresher));
    onMounted(() => { load(); checkService(); loadLastProgress(); });
    return { first, last, chat, state, filter, data, loading, svc, playing, chatOptions, running, busy, prog, failures, shown,
             load, more, play, start, stop, checkService, mediaUrl, fmtInt, fmtDur, fmtTime, pct, stateText, stateKind };
  },
};
