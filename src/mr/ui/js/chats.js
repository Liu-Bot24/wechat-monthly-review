/* 读取范围 (会话选择): which chats the monthly reports read, or one 专题's own choice (#/chats/<专题>).
   Rules (strongest first): archive excluded > excluded by hand > category skipped unless kept by hand > read. */
const catName = catTitle;
const ROW_H = 30;
const COLS = [
  { key: 'read', label: '读取', w: 48, sort: 'read' },
  { key: 'name', label: '名称', w: 230, sort: 'name' },
  { key: 'category', label: '分类', w: 120, sort: 'category' },
  { key: 'type', label: '类型', w: 50, sort: 'group' },
  { key: 'messages', label: '近 12 月', w: 80, sort: 'messages', r: true },
  { key: 'mine', label: '我发', w: 60, sort: 'mine', r: true },
  { key: 'last', label: '最后活跃', w: 92, sort: 'last' },
  { key: 'reason', label: '状态', w: 100, sort: 'reason' },
];

MRPages.chats = {
  components: { Icon, Modal, Picker },
  template: `
<div class="page full" @keydown="onPageKey">
  <div class="page-head" style="padding:12px 18px 0;margin-bottom:10px">
    <button class="btn ghost sm" @click="back"><Icon name="back"/>返回</button>
    <h1>{{ owner }}<span class="crumb">›</span>读取范围</h1>
    <span class="sub">{{ !topicId ? '所有月份共用这份选择。不读的会话在取数时就丢弃，内容不会发给模型。' : ownTopic ? '只用于这个专题，和月报的读取范围互不影响。' : '' }}</span>
    <span class="spacer"></span>
    <button class="btn ghost sm" @click="help = true"><Icon name="info"/>快捷键</button>
  </div>
  <div class="banner warn" v-if="topic && topic.follow_monthly" style="margin:0 18px 10px">
    <Icon name="info"/><div class="grow">这个专题沿用月报的读取范围，这里的修改会同时影响月报。</div>
    <button class="btn sm" @click="useOwn">改用专题自己的范围</button></div>
  <div class="pblock" v-if="ownTopic && rows.length" :class="{open: work.people.on}">
    <label class="phead"><input type="checkbox" :checked="work.people.on" @change="setPeople('on', !work.people.on)">
      <b>联系人在群聊里的发言</b>
      <span class="muted small">{{ work.people.on ? '对象 ' + people.size + ' 人' : '打开后，按选中的联系人去群聊里找他们说的话' }}</span></label>
    <div class="pbody" v-if="work.people.on">
      <span class="k">对象</span>
      <div class="row wrap" style="gap:6px">
        <span class="small">勾选的私聊联系人 <b class="num">{{ tickedPeople.length }}</b> 人</span>
        <span class="psep"></span>
        <span class="small muted">添加</span>
        <span v-for="(c, i) in work.people.add" :key="c.id" class="chip accent">{{ c.name }}<span class="muted" v-if="c.category === '群成员'">（群成员）</span><a href="" @click.prevent="dropRef('add', i)" title="去掉">×</a></span>
        <Picker kind="person" placeholder="输入名字" @pick="c => addRef('add', c)"/>
        <span class="psep"></span>
        <span class="small muted">排除</span>
        <span v-for="(c, i) in work.people.remove" :key="c.id" class="chip bad">{{ c.name }}<a href="" @click.prevent="dropRef('remove', i)" title="去掉">×</a></span>
        <Picker :source="tickedSearch" placeholder="输入名字" @pick="c => addRef('remove', c)"/>
      </div>
      <span class="k">范围</span>
      <div class="col" style="gap:5px;align-items:flex-start">
        <label class="row small"><input type="radio" :checked="work.people.where === 'all'" @change="setPeople('where', 'all')"><b>所有群聊</b><span class="muted">在他们所在的全部群里找；勾选的群照常整群读</span></label>
        <label class="row small"><input type="radio" :checked="work.people.where === 'picked'" @change="setPeople('where', 'picked')"><b>只在勾选的群聊</b><span class="muted">勾选的群只读这些人的发言和前后上下文</span>
          <span class="pwarn" v-if="work.people.where === 'picked' && !pickedGroups">还没有勾选群聊</span></label>
      </div>
      <span></span>
      <label class="row small special"><input type="checkbox" :checked="work.people.skip_private" @change="setPeople('skip_private', !work.people.skip_private)">不读他们的私聊，只要群聊里的发言</label>
    </div>
  </div>
  <div v-if="!rows.length" class="empty"><span class="spinner"></span> 正在统计各会话最近 12 个月的消息（第一次需要几秒钟）…</div>
  <div class="chats" v-else style="border-top:1px solid var(--line)">
    <div class="ctree">
      <div class="tnode" :class="{on: !cat}" @click="cat = null">
        <input type="checkbox" :checked="allState === 'all'" :indeterminate.prop="allState === 'some'" @click.stop="toggleAll" :title="allState === 'none' ? '全部读取' : '全部不读'">
        <b>全部会话</b><span class="cnt">读 {{ counts.all.read }} / {{ counts.all.total }}</span></div>
      <div v-for="n in tree" :key="n.key" class="tnode" :class="{on: cat === n.key}" :style="{paddingLeft: 8 + n.depth * 20 + 'px'}" @click="cat = n.key" :title="n.key">
        <input type="checkbox" :checked="nodeState(n.key) === 'all'" :indeterminate.prop="nodeState(n.key) === 'some'" @click.stop="toggleNode(n.key)"
               :title="nodeState(n.key) === 'all' ? '整类跳过' : '整类读取'">
        <span class="grow ellipsis">{{ n.label }}</span>
        <span class="rule" v-if="ruleOn(n.key)" title="这一类设置了整类跳过；手动保留的会话仍然会读">整类跳过</span>
        <span class="cnt">{{ counts[n.key]?.read || 0 }} / {{ counts[n.key]?.total || 0 }}</span></div>
      <div class="muted small" style="padding:12px 8px">勾选表示读取。取消勾选一个分类就是设置"整类跳过"，以后新出现的这类会话也不读；其中手动保留的会话仍然会读。</div>
    </div>

    <div class="clist">
      <div class="cfilters">
        <div class="row" style="position:relative">
          <input class="input" ref="search" v-model="q" placeholder="搜索名称、微信号、拼音或首字母" style="width:260px;padding-left:28px" @keydown.esc.stop="q ? q = '' : $refs.list.focus()" @keydown.down.prevent="$refs.list.focus()">
          <span style="position:absolute;left:8px;top:7px;color:var(--text-3)"><Icon name="search" :size="15"/></span>
        </div>
        <span class="chip click" :class="{on: f.active}" @click="f.active = !f.active">近 12 月有消息</span>
        <div class="seg sm"><button :class="{on: f.type === 'all'}" @click="f.type = 'all'">全部</button><button :class="{on: f.type === 'private'}" @click="f.type = 'private'">私聊</button><button :class="{on: f.type === 'group'}" @click="f.type = 'group'">群</button></div>
        <div class="seg sm"><button :class="{on: f.state === 'all'}" @click="f.state = 'all'">全部</button><button :class="{on: f.state === 'read'}" @click="f.state = 'read'">读取</button><button :class="{on: f.state === 'skip'}" @click="f.state = 'skip'">不读</button></div>
        <span class="chip click" :class="{on: f.spoke === 'never'}" @click="f.spoke = f.spoke === 'never' ? '' : 'never'">我从没发过言</span>
        <span class="chip click" :class="{on: f.spoke === 'yes'}" @click="f.spoke = f.spoke === 'yes' ? '' : 'yes'">我发过言</span>
        <span class="chip click" :class="{on: f.manual}" @click="f.manual = !f.manual">手动设置过的</span>
        <span class="row small">消息 ≥<input class="input sm num" style="width:64px" v-model.number="f.min" placeholder="0"></span>
        <span class="row small">最近<input class="input sm num" style="width:44px" v-model.number="f.quiet" placeholder="—">个月没有动静</span>
        <button class="btn ghost sm" v-if="filtered" @click="resetFilters">清除筛选</button>
      </div>
      <div class="bulk" v-if="sel.size">
        <b>已选 {{ sel.size }} 个</b>
        <button class="btn sm" @click="applyRead(selectedRows, false)">不读（{{ sel.size }}）</button>
        <button class="btn sm" @click="applyRead(selectedRows, true)">读取（{{ sel.size }}）</button>
        <button class="btn sm" @click="applyReset(selectedRows)">恢复为分类默认（{{ sel.size }}）</button>
        <span class="spacer"></span>
        <button class="btn ghost sm" @click="sel = new Set()">取消选择</button>
      </div>
      <div style="overflow:hidden;flex:none"><div class="vhead" :style="{width: totalW + 'px', transform: 'translateX(' + (-scrollLeft) + 'px)'}">
        <div v-for="(c, i) in cols" :key="c.key" :style="{width: ws[i] + 'px', textAlign: c.r ? 'right' : 'left'}" @click="sortBy(c.sort)">
          {{ c.label }}<span v-if="sort.key === c.sort">{{ sort.dir > 0 ? ' ↑' : ' ↓' }}</span>
          <span class="rz" @mousedown.stop.prevent="startResize(i, $event)" @click.stop></span></div>
      </div></div>
      <div class="vbody" ref="list" tabindex="0" @scroll="onScroll" @keydown="onListKey" @mousedown="onListDown">
        <div :style="{height: view.length * ROW_H + 'px', width: totalW + 'px', position: 'relative'}">
          <div v-for="(r, k) in visible" :key="r.chat" class="vrow" :data-i="first + k"
               :class="{sel: sel.has(r.chat), focus: focus === first + k, skip: !!reasonOf(r), locked: r.locked}"
               :style="{top: (first + k) * ROW_H + 'px', height: ROW_H + 'px', width: totalW + 'px'}">
            <div :style="{width: ws[0] + 'px'}" class="brush" data-brush="1" :title="r.locked ? '归档设置为不归档，不能修改' : '点击切换；按住拖动可以连续设置'">
              <input type="checkbox" :checked="!reasonOf(r)" :disabled="r.locked" tabindex="-1" style="pointer-events:none"></div>
            <div :style="{width: ws[1] + 'px'}" class="nm" :title="r.name + '\\n' + r.chat">{{ r.name }}</div>
            <div :style="{width: ws[2] + 'px'}" class="muted small">{{ catName(r.category) }}</div>
            <div :style="{width: ws[3] + 'px'}" class="muted small">{{ r.group ? '群' : '私聊' }}</div>
            <div :style="{width: ws[4] + 'px', textAlign: 'right'}" class="num">{{ r.messages ? fmtInt(r.messages) : '' }}</div>
            <div :style="{width: ws[5] + 'px', textAlign: 'right'}" class="num muted">{{ r.mine || '' }}</div>
            <div :style="{width: ws[6] + 'px'}" class="num muted small">{{ fmtDate(r.last) }}</div>
            <div :style="{width: ws[7] + 'px'}">
              <span class="chip bad" v-if="manualOf(r) === 'exclude'">手动排除</span>
              <span class="chip ok" v-else-if="manualOf(r) === 'include'">手动保留</span>
              <span class="chip" v-else-if="r.locked">归档策略排除</span>
              <span class="muted small" v-else-if="reasonOf(r)">整类跳过</span>
            </div>
          </div>
        </div>
        <div class="empty" v-if="!view.length" style="position:absolute;inset:40px 0 auto">没有符合条件的会话</div>
      </div>
      <div class="cstatus">
        <span>读取 <b class="num">{{ fmtInt(counts.all.read) }}</b> 个会话，最近 12 个月共 <b class="num">{{ fmtInt(counts.readMessages) }}</b> 条消息</span>
        <span class="muted">列表 {{ fmtInt(view.length) }} 个</span>
        <span v-if="diff.added || diff.removed" class="muted">与已保存相比：<span v-if="diff.added" style="color:var(--ok)">+{{ diff.added }} 个读取</span> <span v-if="diff.removed" style="color:var(--bad)">−{{ diff.removed }} 个不读</span></span>
        <span class="spacer"></span>
        <button class="btn ghost sm icon" title="撤销（Ctrl+Z）" :disabled="!undo.length" @click="doUndo"><Icon name="undo"/></button>
        <button class="btn ghost sm icon" title="重做（Ctrl+Y）" :disabled="!redo.length" @click="doRedo"><Icon name="redo"/></button>
        <span class="dirty" v-if="dirty">● 有未保存的修改</span>
        <button class="btn sm" :disabled="!dirty" @click="discard">放弃修改</button>
        <button class="btn primary sm" :disabled="!dirty || saving" @click="save">保存（Ctrl+S）</button>
      </div>
    </div>

    <div class="cpreview">
      <template v-if="cur">
        <div class="sec">
          <div style="font-weight:600;font-size:14px">{{ cur.name }}</div>
          <div class="muted small mono" style="word-break:break-all">{{ cur.chat }}</div>
          <div class="muted small">{{ cur.folder }}</div>
          <div style="margin-top:8px" class="row wrap">
            <span class="chip" :class="reasonOf(cur) ? 'bad' : 'ok'">{{ reasonOf(cur) ? '不读：' + reasonOf(cur) : '读取' }}</span>
            <button class="btn sm" v-if="!cur.locked" @click="applyRead([cur], !!reasonOf(cur))">{{ reasonOf(cur) ? '改为读取' : '改为不读' }}</button>
            <button class="btn sm ghost" v-if="manualOf(cur)" @click="applyReset([cur])">恢复为分类默认</button>
          </div>
        </div>
        <div class="sec">
          <h3>最近 12 个月每月消息</h3>
          <div class="minibars"><div v-for="(v, i) in cur.monthly" :key="i" :style="{height: barH(v) + '%'}" :title="months[i] + '：' + v + ' 条'"></div></div>
          <div class="minilabels"><span v-for="m in months" :key="m">{{ +m.slice(5) }}</span></div>
        </div>
        <div class="sec" v-if="preview && preview.mentions.length">
          <h3>最近几期月报中</h3>
          <div v-for="(x, i) in preview.mentions" :key="i" class="small" style="margin-bottom:4px"><span class="chip" :class="x.kind === '@我' ? 'accent' : 'warn'">{{ x.kind }}</span> {{ x.month }} {{ x.text }}</div>
        </div>
        <div class="sec">
          <h3>最近 20 条消息</h3>
          <div v-if="!preview" class="muted"><span class="spinner"></span></div>
          <div v-else-if="!preview.recent.length" class="muted small">没有消息</div>
          <div v-else style="background:var(--bg);padding:6px;border-radius:6px">
            <div v-for="m in preview.recent" :key="m.ts + m.sender" class="msg" :class="{me: m.self}" style="max-width:100%">
              <div class="who">{{ m.sender }} · {{ m.time.slice(0, 16) }}</div>
              <div class="bub small">{{ m.body.length > 300 ? m.body.slice(0, 300) + '…' : m.body }}</div>
            </div>
          </div>
        </div>
      </template>
      <div v-else class="empty">选中一个会话，在这里查看它的近况</div>
    </div>
  </div>

  <Modal v-if="warnings" title="这些会话最近和你有关" @close="warnings = null">
    <p>下面的会话将不再读取，但最近几期月报里有 @我 或者还没办完的事。仍然保存吗？</p>
    <div v-for="w in warnings" :key="w.chat" style="margin:8px 0">
      <b>{{ w.name }}</b>
      <div v-for="(x, i) in w.items" :key="i" class="small muted">· {{ x.month }} {{ x.kind }}：{{ x.text }}</div>
    </div>
    <template #foot><button class="btn" @click="warnings = null">返回修改</button><button class="btn primary" @click="commit">仍然保存</button></template>
  </Modal>
  <Modal v-if="help" title="读取范围的快捷操作" @close="help = false">
    <table class="t small"><tbody>
      <tr><td>单击 / Shift+单击 / Ctrl+单击</td><td>选中一行 / 选中一个范围 / 逐个增减</td></tr>
      <tr><td>在列表上按住拖动</td><td>连续选中多行</td></tr>
      <tr><td>点"全部会话"前的勾选框</td><td>一个都没读时全部读取；已经读了一些时全部不读</td></tr>
      <tr><td>在"读取"列上按住往下拖</td><td>经过的行都设成和第一行相同的状态（刷选）</td></tr>
      <tr><td><span class="kbd">Ctrl</span>+<span class="kbd">A</span></td><td>选中当前筛选结果中的全部</td></tr>
      <tr><td><span class="kbd">空格</span></td><td>切换读或不读</td></tr>
      <tr><td><span class="kbd">X</span> 或 <span class="kbd">Delete</span></td><td>设为不读</td></tr>
      <tr><td><span class="kbd">I</span></td><td>设为读取</td></tr>
      <tr><td><span class="kbd">R</span></td><td>恢复为分类默认</td></tr>
      <tr><td><span class="kbd">↑</span> <span class="kbd">↓</span>（加 Shift 扩展选择）</td><td>移动</td></tr>
      <tr><td><span class="kbd">Ctrl</span>+<span class="kbd">Z</span> / <span class="kbd">Ctrl</span>+<span class="kbd">Y</span></td><td>撤销 / 重做</td></tr>
      <tr><td><span class="kbd">Ctrl</span>+<span class="kbd">S</span></td><td>保存</td></tr>
      <tr><td><span class="kbd">Ctrl</span>+<span class="kbd">F</span> 或 <span class="kbd">/</span></td><td>跳到搜索框；<span class="kbd">Esc</span> 清空</td></tr>
    </tbody></table>
  </Modal>
</div>`,
  setup() {
    const rows = ref([]), months = ref([]), cats = ref([]);
    const topicId = route.arg || '';
    const topic = ref(null);                  // the 专题 whose 读取范围 this is, when it is one
    const ownTopic = computed(() => !!topic.value && !topic.value.follow_monthly);
    const owner = computed(() => !topicId ? '月报' : topic.value ? topic.value.title || '未命名专题' : '专题');
    const saved = ref(null);                  // selection as stored
    const work = reactive({ skip: [], exclude: {}, include: {}, people: null });
    const undo = ref([]), redo = ref([]);
    const q = ref(''), cat = ref(null);
    const f = reactive(loadLocal('chats.filters', { active: true, type: 'all', state: 'all', spoke: '', manual: false, min: null, quiet: null }));
    watch(f, v => saveLocal('chats.filters', v), { deep: true });
    const sort = reactive(loadLocal('chats.sort', { key: 'messages', dir: -1 }));
    watch(sort, v => saveLocal('chats.sort', v), { deep: true });
    const widths = loadLocal('chats.widths', {});
    const cols = reactive(COLS.map(c => ({ ...c, w: widths[c.key] || c.w })));
    // Width the window has beyond the columns goes mostly to the name, some to category and status.
    const listW = ref(0);
    const GROW = { 1: 0.6, 2: 0.2, 7: 0.2 };
    const ws = computed(() => {
      const w = cols.map(c => c.w), extra = listW.value - w.reduce((a, b) => a + b, 0);
      if (extra > 0) for (const i in GROW) w[i] += Math.floor(extra * GROW[i]);
      return w;
    });
    const totalW = computed(() => ws.value.reduce((a, b) => a + b, 0));
    const sel = ref(new Set()), focus = ref(-1), anchor = ref(-1);
    const list = ref(null), search = ref(null);
    const scrollTop = ref(0), height = ref(600);
    const preview = ref(null), warnings = ref(null), help = ref(false), saving = ref(false);

    const snapshot = () => JSON.stringify({ skip: [...work.skip].sort(), exclude: work.exclude, include: work.include, people: work.people });
    const skipSet = computed(() => new Set(work.skip));
    const catSkipped = c => catAncestors(c).some(k => skipSet.value.has(k));
    function reasonFor(r, w) {
      if (r.locked) return '归档策略排除';
      if (r.chat in w.exclude) return '手动排除';
      const skipped = catAncestors(r.category).some(k => w.skip.includes(k));
      if (skipped && !(r.chat in w.include)) return '整类跳过';
      return null;
    }
    const reasonOf = r => {
      if (r.locked) return '归档策略排除';
      if (r.chat in work.exclude) return '手动排除';
      if (catSkipped(r.category) && !(r.chat in work.include)) return '整类跳过';
      return null;
    };
    const manualOf = r => r.chat in work.exclude ? 'exclude' : r.chat in work.include ? 'include' : null;

    async function load() {
      try {
        const d = await api('/api/chats' + (topicId ? '?topic=' + encodeURIComponent(topicId) : ''));
        months.value = d.months;
        d.chats.forEach(r => { r.lname = r.name.toLowerCase(); r.lchat = r.chat.toLowerCase(); });
        rows.value = d.chats;
        cats.value = d.categories;
        topic.value = d.topic || null;
        setSaved(d.selection, ownTopic.value ? d.topic.people : null);
      } catch (e) { fail(e); }
    }
    // Top-level folders: skipping all of them reads nothing, which is where a 专题's own choice starts.
    const topLevel = computed(() => [...new Set([...cats.value, ...rows.value.map(r => r.category)].map(c => c.split('/')[0]))].sort());
    function setSaved(s, people) {
      s = s || { skip_categories: topLevel.value, exclude: {}, include: {} };
      work.skip = [...s.skip_categories]; work.exclude = { ...s.exclude }; work.include = { ...s.include };
      work.people = people ? JSON.parse(JSON.stringify(people)) : null;
      saved.value = snapshot();
      undo.value = []; redo.value = [];
    }
    const dirty = computed(() => saved.value !== null && snapshot() !== saved.value);
    const guard = () => dirty.value ? '读取范围有未保存的修改，离开后会丢失。' : null;
    onMounted(() => { leaveGuards.push(guard); load(); measure(); window.addEventListener('resize', measure); });
    onBeforeUnmount(() => { leaveGuards.splice(leaveGuards.indexOf(guard), 1); window.removeEventListener('resize', measure); });

    // ----- edits, each one undoable -----
    function change(fn) {
      const before = snapshot();
      fn();
      if (snapshot() !== before) { undo.value.push(before); if (undo.value.length > 200) undo.value.shift(); redo.value = []; }
    }
    function restore(s) { const o = JSON.parse(s); work.skip = o.skip; work.exclude = o.exclude; work.include = o.include; work.people = o.people; }
    function doUndo() { if (!undo.value.length) return; redo.value.push(snapshot()); restore(undo.value.pop()); }
    function doRedo() { if (!redo.value.length) return; undo.value.push(snapshot()); restore(redo.value.pop()); }
    function setOne(r, read) {
      if (r.locked) return;
      delete work.exclude[r.chat]; delete work.include[r.chat];
      const skipped = catSkipped(r.category);
      if (read && skipped) work.include[r.chat] = r.name;
      if (!read && !skipped) work.exclude[r.chat] = r.name;
    }
    function applyRead(rs, read) { change(() => rs.forEach(r => setOne(r, read))); }
    function applyReset(rs) { change(() => rs.forEach(r => { delete work.exclude[r.chat]; delete work.include[r.chat]; })); }
    const inNode = (r, key) => catWithin(r.category, key);
    function nodeState(key) {
      const c = counts.value[key];
      if (!c || !c.total) return key.split('/').some((_, i, p) => ruleOn(p.slice(0, i + 1).join('/'))) ? 'none' : 'all';
      return c.read === c.total ? 'all' : c.read === 0 ? 'none' : 'some';
    }
    const ruleOn = key => skipSet.value.has(key);
    function toggleNode(key) {
      const toSkip = nodeState(key) === 'all';
      change(() => {
        const members = rows.value.filter(r => inNode(r, key));
        if (toSkip) {
          if (!work.skip.includes(key)) work.skip.push(key);
          members.forEach(r => { delete work.include[r.chat]; });
        } else {
          work.skip = work.skip.filter(c => !catWithin(c, key));
          // Reading a folder inside a skipped one: lift the outer rule and skip the rest of that folder instead.
          const path = catAncestors(key);
          path.slice(0, -1).forEach((outer, depth) => {
            if (!work.skip.includes(outer)) return;
            work.skip = work.skip.filter(c => c !== outer);
            for (let i = depth; i < path.length - 1; i++) {
              for (const sib of childrenOf(path[i])) if (sib !== path[i + 1] && !work.skip.includes(sib)) work.skip.push(sib);
              // Chats sitting directly in a folder on the way down have no folder of their own to skip.
              rows.value.filter(r => r.category === path[i] && !r.locked && !(r.chat in work.include)).forEach(r => { work.exclude[r.chat] = r.name; });
            }
          });
          members.forEach(r => { delete work.exclude[r.chat]; });
        }
      });
    }

    // "全部会话": nothing read yet reads everything; anything read clears it all.
    const allState = computed(() => {
      let read = 0, total = 0;
      for (const r of rows.value) if (!r.locked) { total++; if (!reasonOf(r)) read++; }
      return read === 0 ? 'none' : read === total ? 'all' : 'some';
    });
    function toggleAll() {
      const clear = allState.value !== 'none';
      change(() => { work.skip = clear ? [...topLevel.value] : []; work.exclude = {}; work.include = {}; });
    }

    // ----- a 专题's people block -----
    const tickedPeople = computed(() => rows.value.filter(r => !r.group && r.chat !== 'filehelper' && !reasonOf(r)));
    const people = computed(() => {
      if (!work.people) return new Set();
      const out = new Set([...tickedPeople.value.map(r => r.chat), ...work.people.add.map(p => p.id)]);
      work.people.remove.forEach(p => out.delete(p.id));
      return out;
    });
    const pickedGroups = computed(() => rows.value.filter(r => r.group && !reasonOf(r)).length);
    function setPeople(k, v) { change(() => { work.people[k] = v; }); }
    function addRef(list, c) {
      change(() => {
        const other = list === 'add' ? 'remove' : 'add';
        work.people[other] = work.people[other].filter(p => p.id !== c.id);
        if (!work.people[list].some(p => p.id === c.id)) work.people[list].push({ id: c.id, name: c.name, category: c.category });
      });
    }
    function dropRef(list, i) { change(() => { work.people[list].splice(i, 1); }); }
    function tickedSearch(v) {
      const low = v.toLowerCase(), out = new Set(work.people.remove.map(p => p.id));
      return tickedPeople.value.filter(r => !out.has(r.chat) && (r.lname.includes(low) || (r.py && r.py.includes(low)) || (r.pyi && r.pyi.includes(low))))
        .slice(0, 40).map(r => ({ id: r.chat, name: r.name, category: r.category }));
    }

    // ----- filtering, sorting, counting -----
    const quietCut = computed(() => f.quiet ? months.value.length - f.quiet : null);
    function passes(r, withCat = true) {
      if (withCat && cat.value && !inNode(r, cat.value)) return false;
      if (f.active && !r.messages) return false;
      if (f.type === 'private' && r.group) return false;
      if (f.type === 'group' && !r.group) return false;
      if (f.spoke === 'never' && r.mine) return false;
      if (f.spoke === 'yes' && !r.mine) return false;
      if (f.manual && !manualOf(r)) return false;
      if (f.min && r.messages < f.min) return false;
      if (quietCut.value !== null && r.monthly.slice(Math.max(0, quietCut.value)).some(v => v)) return false;
      if (f.state === 'read' && reasonOf(r)) return false;
      if (f.state === 'skip' && !reasonOf(r)) return false;
      const s = q.value.trim().toLowerCase();
      if (s && !(r.lname.includes(s) || r.lchat.includes(s) || (r.py && r.py.includes(s)) || (r.pyi && r.pyi.startsWith(s)) || (r.pyi && s.length >= 2 && r.pyi.includes(s)))) return false;
      return true;
    }
    const view = computed(() => {
      const out = rows.value.filter(r => passes(r));
      const k = sort.key, d = sort.dir;
      const val = r => k === 'read' ? (reasonOf(r) ? 1 : 0) : k === 'reason' ? (reasonOf(r) || '') + (manualOf(r) || '') : k === 'group' ? (r.group ? 1 : 0) : r[k] ?? (k === 'last' ? 0 : '');
      const cmp = (x, y) => typeof x === 'string' ? x.localeCompare(y, 'zh') : x - y;
      out.sort((a, b) => (cmp(val(a), val(b)) || a.name.localeCompare(b.name, 'zh') * d) * d);
      return out;
    });
    const filtered = computed(() => q.value || !f.active || f.type !== 'all' || f.state !== 'all' || f.spoke || f.manual || f.min || f.quiet);
    function resetFilters() { q.value = ''; Object.assign(f, { active: true, type: 'all', state: 'all', spoke: '', manual: false, min: null, quiet: null }); }
    // Folders from the archive directory, so a new or still empty one shows too.
    const tree = computed(() => [...new Set([...cats.value, ...rows.value.flatMap(r => catAncestors(r.category))])].sort()
      .map(k => { const parts = k.split('/'); return { key: k, depth: parts.length - 1, label: parts.length === 1 ? catTitle(k) : catPart(parts.at(-1)) }; }));
    const childrenOf = key => tree.value.filter(n => n.key.startsWith(key + '/') && n.depth === key.split('/').length).map(n => n.key);
    const counts = computed(() => {
      const out = { all: { read: 0, total: 0 }, readMessages: 0 };
      // Counts follow the activity filter only, so a node's numbers do not jump with the search box.
      for (const r of rows.value) {
        if (f.active && !r.messages) continue;
        const read = !reasonOf(r);
        const keys = ['all', ...catAncestors(r.category)];
        for (const k of keys) { out[k] = out[k] || { read: 0, total: 0 }; out[k].total++; if (read) out[k].read++; }
        if (read) out.readMessages += r.messages;
      }
      return out;
    });
    const diff = computed(() => {
      if (!saved.value) return { added: 0, removed: 0 };
      const old = JSON.parse(saved.value);
      let added = 0, removed = 0;
      for (const r of rows.value) {
        const a = !reasonFor(r, old), b = !reasonOf(r);
        if (a !== b) b ? added++ : removed++;
      }
      return { added, removed };
    });
    function sortBy(k) { if (sort.key === k) sort.dir = -sort.dir; else { sort.key = k; sort.dir = ['name', 'category', 'reason'].includes(k) ? 1 : -1; } }
    // A selection made under another filter would act on rows no longer shown, so it is dropped.
    watch(() => [q.value, cat.value, JSON.stringify(f)], () => { sel.value = new Set(); focus.value = -1; anchor.value = -1; if (list.value) list.value.scrollTop = 0; });

    // ----- virtual list -----
    function measure() { if (list.value) { height.value = list.value.clientHeight; listW.value = list.value.clientWidth; } }
    watch(list, () => nextTick(measure));
    watch(list, measure);
    const scrollLeft = ref(0);
    function onScroll() { scrollTop.value = list.value.scrollTop; scrollLeft.value = list.value.scrollLeft; }
    const first = computed(() => Math.max(0, Math.floor(scrollTop.value / ROW_H) - 8));
    const visible = computed(() => view.value.slice(first.value, first.value + Math.ceil(height.value / ROW_H) + 16));
    const selectedRows = computed(() => view.value.filter(r => sel.value.has(r.chat)).concat(rows.value.filter(r => sel.value.has(r.chat) && !view.value.includes(r))));
    const cur = computed(() => view.value[focus.value] || null);
    watch(cur, async r => {
      preview.value = null;
      if (!r) return;
      const chat = r.chat;
      try { const p = await api(`/api/chats/${encodeURIComponent(chat)}/preview`); if (cur.value && cur.value.chat === chat) preview.value = p; } catch (e) { preview.value = { recent: [], mentions: [] }; }
    });
    function ensureVisible(i) {
      const el = list.value; if (!el) return;
      const top = i * ROW_H, bottom = top + ROW_H;
      if (top < el.scrollTop) el.scrollTop = top;
      else if (bottom > el.scrollTop + el.clientHeight) el.scrollTop = bottom - el.clientHeight;
    }
    const rowAt = ev => {
      const rect = list.value.getBoundingClientRect();
      return Math.floor((ev.clientY - rect.top + list.value.scrollTop) / ROW_H);
    };
    function selectRange(a, b, add) {
      const [lo, hi] = [Math.min(a, b), Math.max(a, b)];
      const s = add ? new Set(sel.value) : new Set();
      view.value.slice(lo, hi + 1).forEach(r => s.add(r.chat));
      sel.value = s;
    }
    // Mouse: click/shift/ctrl select; drag selects a range; dragging in the 读取 column paints one state.
    let drag = null, autoScroll = null;
    function onListDown(ev) {
      if (ev.button !== 0) return;
      const i = rowAt(ev);
      if (i < 0 || i >= view.value.length) { sel.value = new Set(); return; }
      const r = view.value[i];
      list.value.focus();
      if (ev.target.closest('[data-brush]')) {
        if (r.locked) return;
        const read = !!reasonOf(r);
        undo.value.push(snapshot()); redo.value = [];
        setOne(r, read);
        drag = { brush: read, done: new Set([r.chat]) };
      } else {
        if (ev.shiftKey && anchor.value >= 0) selectRange(anchor.value, i, ev.ctrlKey);
        else if (ev.ctrlKey || ev.metaKey) { const s = new Set(sel.value); s.has(r.chat) ? s.delete(r.chat) : s.add(r.chat); sel.value = s; anchor.value = i; }
        else { sel.value = new Set([r.chat]); anchor.value = i; }
        drag = { start: ev.shiftKey ? anchor.value : i, add: ev.ctrlKey };
      }
      focus.value = i;
      window.addEventListener('mousemove', onDragMove);
      window.addEventListener('mouseup', onDragEnd, { once: true });
    }
    function onDragMove(ev) {
      if (!drag) return;
      const rect = list.value.getBoundingClientRect();
      clearInterval(autoScroll);
      if (ev.clientY > rect.bottom - 20 || ev.clientY < rect.top + 20) {
        const d = ev.clientY > rect.bottom - 20 ? ROW_H : -ROW_H;
        autoScroll = setInterval(() => { list.value.scrollTop += d; dragTo(ev); }, 40);
      }
      dragTo(ev);
    }
    function dragTo(ev) {
      const i = Math.max(0, Math.min(view.value.length - 1, rowAt(ev)));
      if (drag.brush !== undefined) {
        const lo = Math.min(focus.value, i), hi = Math.max(focus.value, i);
        view.value.slice(lo, hi + 1).forEach(r => { if (!drag.done.has(r.chat)) { setOne(r, drag.brush); drag.done.add(r.chat); } });
      } else if (i !== drag.start || sel.value.size > 1) {
        selectRange(drag.start, i, drag.add);
      }
    }
    function onDragEnd() {
      clearInterval(autoScroll);
      window.removeEventListener('mousemove', onDragMove);
      if (drag && drag.brush !== undefined && undo.value.length && undo.value[undo.value.length - 1] === snapshot()) undo.value.pop();
      drag = null;
    }
    function move(i, extend) {
      i = Math.max(0, Math.min(view.value.length - 1, i));
      if (extend) { if (anchor.value < 0) anchor.value = focus.value >= 0 ? focus.value : i; selectRange(anchor.value, i, false); }
      else { sel.value = new Set([view.value[i].chat]); anchor.value = i; }
      focus.value = i; ensureVisible(i);
    }
    const targets = () => sel.value.size ? selectedRows.value : cur.value ? [cur.value] : [];
    function onListKey(ev) {
      const k = ev.key, ctrl = ev.ctrlKey || ev.metaKey;
      if (!view.value.length) return;
      if (k === 'ArrowDown') { ev.preventDefault(); move(focus.value + 1, ev.shiftKey); }
      else if (k === 'ArrowUp') { ev.preventDefault(); move(focus.value - 1, ev.shiftKey); }
      else if (k === 'PageDown') { ev.preventDefault(); move(focus.value + Math.floor(height.value / ROW_H), ev.shiftKey); }
      else if (k === 'PageUp') { ev.preventDefault(); move(focus.value - Math.floor(height.value / ROW_H), ev.shiftKey); }
      else if (k === 'Home') { ev.preventDefault(); move(0, ev.shiftKey); }
      else if (k === 'End') { ev.preventDefault(); move(view.value.length - 1, ev.shiftKey); }
      else if (ctrl && k.toLowerCase() === 'a') { ev.preventDefault(); sel.value = new Set(view.value.map(r => r.chat)); }
      else if (k === ' ') { ev.preventDefault(); const t = targets(); if (t.length) applyRead(t, !!reasonOf(cur.value || t[0])); }
      else if (!ctrl && (k === 'x' || k === 'X' || k === 'Delete')) { ev.preventDefault(); applyRead(targets(), false); }
      else if (!ctrl && (k === 'i' || k === 'I')) { ev.preventDefault(); applyRead(targets(), true); }
      else if (!ctrl && (k === 'r' || k === 'R')) { ev.preventDefault(); applyReset(targets()); }
      else if (k === 'Escape') { sel.value = new Set(); }
    }
    function onPageKey(ev) {
      const k = ev.key.toLowerCase(), ctrl = ev.ctrlKey || ev.metaKey;
      const typing = ['INPUT', 'TEXTAREA'].includes(ev.target.tagName) && ev.target.type !== 'checkbox';
      if (ctrl && k === 's') { ev.preventDefault(); if (dirty.value) save(); }
      else if (ctrl && k === 'f') { ev.preventDefault(); search.value.focus(); search.value.select(); }
      else if (!typing && k === '/') { ev.preventDefault(); search.value.focus(); }
      else if (!typing && ctrl && k === 'z') { ev.preventDefault(); ev.shiftKey ? doRedo() : doUndo(); }
      else if (!typing && ctrl && k === 'y') { ev.preventDefault(); doRedo(); }
    }
    // Column widths
    function startResize(i, ev) {
      const x0 = ev.clientX, w0 = ws.value[i];
      // Dragging sets the base width; the share of spare width is added on top again.
      const spare = w0 - cols[i].w;
      const mv = e => { cols[i].w = Math.max(40, w0 + e.clientX - x0 - spare); };
      window.addEventListener('mousemove', mv);
      window.addEventListener('mouseup', () => { window.removeEventListener('mousemove', mv); saveLocal('chats.widths', Object.fromEntries(cols.map(c => [c.key, c.w]))); }, { once: true });
    }
    const barH = v => { const m = Math.max(1, ...(cur.value?.monthly || [1])); return v ? Math.max(4, v / m * 100) : 0; };

    // ----- saving -----
    async function save() {
      const old = JSON.parse(saved.value);
      const newlySkipped = rows.value.filter(r => !reasonFor(r, old) && reasonOf(r)).map(r => r.chat);
      if (newlySkipped.length && !ownTopic.value) {
        try {
          const r = await api('/api/selection/check', { method: 'POST', body: { chats: newlySkipped } });
          if (r.warnings.length) { warnings.value = r.warnings; return; }
        } catch (e) { fail(e); }
      }
      commit();
    }
    async function commit() {
      warnings.value = null; saving.value = true;
      const selection = { skip_categories: work.skip, exclude: work.exclude, include: work.include };
      try {
        if (ownTopic.value) {
          const r = await api(`/api/topics/${encodeURIComponent(topicId)}/scope`, { method: 'PUT', body: { selection, people: work.people } });
          setSaved(r.spec.scope.selection, r.spec.scope.people);
          toast('读取范围已保存，只用于这个专题。', 'ok');
        } else {
          setSaved(await api('/api/selection', { method: 'PUT', body: selection }));
          toast(topicId ? '月报的读取范围已保存，这个专题和之后生成的月报都按它读取。' : '读取范围已保存，之后生成的月报会按新的选择读取。', 'ok');
          loadOverview();
        }
      } catch (e) { fail(e); }
      saving.value = false;
    }
    function back() { go(topicId ? 'topics' : 'reports', topicId); }
    async function useOwn() {
      if (dirty.value) return toast('先保存或放弃这里的修改', 'warn');
      try {
        await api(`/api/topics/${encodeURIComponent(topicId)}/scope`, { method: 'PUT', body: { follow_monthly: false } });
        rows.value = []; await load();
      } catch (e) { fail(e); }
    }
    async function discard() {
      if (await confirmBox('放弃修改', '放弃这次没有保存的所有修改吗？', { ok: '放弃', danger: true })) restore(saved.value), undo.value = [], redo.value = [];
    }
    return { topicId, topic, ownTopic, owner, work, allState, toggleAll, tickedPeople, people, pickedGroups, setPeople, addRef, dropRef, tickedSearch, back, useOwn,
             rows, months, cols, ws, totalW, scrollLeft, view, visible, first, ROW_H, sel, focus, list, search, q, cat, f, sort, tree, counts, diff, dirty, undo, redo,
             cur, preview, warnings, help, saving, selectedRows, filtered,
             reasonOf, manualOf, nodeState, ruleOn, toggleNode, applyRead, applyReset, resetFilters, sortBy, onScroll, onListDown, onListKey, onPageKey,
             startResize, barH, save, commit, discard, doUndo, doRedo, catName, fmtInt, fmtDate };
  },
};
