/* 设置: providers, models and stage assignment; general, advanced, the configuration files and diagnostics. */
const EFFORTS = [['', '不设置'], ['minimal', 'minimal'], ['low', 'low'], ['medium', 'medium'], ['high', 'high'], ['xhigh', 'xhigh']];
const ANTHROPIC_EFFORTS = [['', '不设置'], ['low', 'low'], ['medium', 'medium'], ['high', 'high'], ['xhigh', 'xhigh'], ['max', 'max']];   // sent as output_config.effort
const NEEDS = { text: ['文本', ''], vision: ['需要看图', 'info'], audio: ['需要语音转写接口', 'accent'] };
const pickFolder = async (initial) => window.pywebview?.api?.pick_folder ? await window.pywebview.api.pick_folder(initial || '') : null;
const pickFile = async (initial) => window.pywebview?.api?.pick_file ? await window.pywebview.api.pick_file(initial || '') : null;
const canPick = () => !!window.pywebview?.api?.pick_folder;

const ProviderDialog = {
  components: { Modal, Icon },
  props: { name: String, provider: Object, apis: Object },
  emits: ['close', 'saved'],
  template: `
<Modal :title="name ? '编辑接口 ' + name : '添加接口'" @close="$emit('close')">
  <div class="form">
    <label class="k">名称</label><input class="input" v-model.trim="f.name">
    <div class="help">只在本程序中用来区分接口，不能有空格和 / \\ : * ? " < > |</div>
    <label class="k">说明</label><input class="input" v-model="f.label" placeholder="可选">
    <label class="k">接口地址</label><input class="input" v-model.trim="f.base_url" placeholder="https://api.openai.com/v1">
    <label class="k">协议</label>
    <select class="input" v-model="f.api"><option v-for="(v, k) in apis" :key="k" :value="k">{{ v }}</option></select>
    <div class="help">GPT-5 以后的 OpenAI 模型用 Responses；多数兼容服务用 Chat Completions；Claude 和提供 Anthropic 格式接口的服务用 Anthropic Messages（地址填到 /v1 之前，带上 /v1 也可以）；Whisper 类语音识别服务选语音转写。</div>
    <label class="k">API Key</label>
    <div class="seg"><button :class="{on: f.key.type === 'none'}" @click="f.key.type = 'none'">不需要</button><button :class="{on: f.key.type === 'credential'}" @click="f.key.type = 'credential'">需要</button></div>
    <template v-if="f.key.type === 'credential'">
      <label class="k">密钥</label><input class="input" type="password" v-model="secret" autocomplete="off" :placeholder="saved ? '已保存；留空表示不修改' : '粘贴 API Key'">
      <div class="help">保存在 Windows 凭据管理器中（仅当前用户可读），不会写进任何文件，也不会随配置导出。</div>
    </template>
  </div>
  <div class="banner" :class="test.ok ? 'ok' : 'bad'" v-if="test" style="margin:14px 0 0">
    <div class="grow" v-if="test.ok && test.note">{{ test.note }}</div>
    <div class="grow" v-else-if="test.ok">连接成功{{ test.ms ? '（' + test.ms + ' 毫秒）' : '' }}，接口提供 {{ test.models.length }} 个模型：{{ test.models.slice(0, 12).join('、') }}{{ test.models.length > 12 ? ' …' : '' }}</div>
    <div class="grow" v-else>{{ test.message }}</div>
  </div>
  <template #foot>
    <button class="btn" @click="probe" :disabled="testing"><span class="spinner" v-if="testing"></span>测试连接</button>
    <span class="spacer"></span>
    <button class="btn" @click="$emit('close')">取消</button>
    <button class="btn primary" @click="save">保存</button>
  </template>
</Modal>`,
  setup(props, { emit }) {
    const p = props.provider || {};
    const f = reactive({ name: props.name || '', label: p.label || '', base_url: p.base_url || '', api: p.api || 'responses',
                         key: { type: p.key?.type || 'none' } });
    const secret = ref(''), test = ref(null), testing = ref(false);
    const saved = p.key_state === 'saved';
    const data = () => ({ label: f.label, base_url: f.base_url, api: f.api, key: { ...f.key } });
    async function probe() {
      testing.value = true; test.value = null;
      try {
        // The form as it stands; a key not typed again is read from where the saved provider keeps it.
        test.value = await api('/api/providers/probe', { method: 'POST', body: { name: props.name || f.name, data: data(), secret: secret.value || undefined } });
      } catch (e) { test.value = { ok: false, message: e.message }; }
      testing.value = false;
    }
    async function save() {
      if (f.key.type === 'credential' && !secret.value && !saved) return toast('请填写密钥，或者选"不需要"', 'warn');
      try {
        const r = await api('/api/providers', { method: 'POST', body: { name: f.name, old_name: props.name || null, data: data(), secret: secret.value || undefined } });
        toast(`接口 ${f.name} 已保存`, 'ok');
        emit('saved', r);
      } catch (e) { fail(e); }
    }
    return { f, secret, test, testing, saved, probe, save };
  },
};

const ModelDialog = {
  components: { Modal, Icon },
  props: { name: String, model: Object, providers: Object, copy: Boolean },
  emits: ['close', 'saved'],
  template: `
<Modal :title="name && !copy ? '编辑模型 ' + name : '添加模型'" @close="$emit('close')" wide>
  <div class="form">
    <label class="k">名称</label><input class="input" v-model.trim="f.name">
    <div class="help">用来在环节中选择。同一个模型配不同参数时，各起一个名称。</div>
    <label class="k">接口</label>
    <select class="input" v-model="f.provider"><option v-for="(p, k) in providers" :key="k" :value="k">{{ k }}{{ p.label ? '（' + p.label + '）' : '' }}</option></select>
    <label class="k">模型名</label>
    <div class="row"><input class="input grow" v-model.trim="f.model" list="model-ids" placeholder="接口中的模型 ID"><datalist id="model-ids"><option v-for="m in remote" :key="m" :value="m"></option></datalist>
      <button class="btn" @click="listRemote" :disabled="!f.provider || listing"><span class="spinner" v-if="listing"></span>读取接口的模型列表</button></div>
    <div class="help" v-if="remote.length">接口提供 {{ remote.length }} 个模型，可以在输入框中选择。</div>
    <template v-if="!isAudio">
      <label class="k">推理强度</label>
      <div class="seg"><button v-for="e in efforts" :key="e[0]" :class="{on: effort === e[0]}" @click="effort = e[0]">{{ e[1] }}</button></div>
      <label class="k">支持图片</label><label class="check"><input type="checkbox" v-model="f.vision">可以用于"看图"环节</label>
    </template>
    <label class="k">其他参数</label><textarea class="input" rows="3" v-model="paramsText" :class="{bad: paramsError}" placeholder="可选，JSON 对象"></textarea>
    <div class="help" :style="paramsError ? 'color:var(--bad)' : ''">{{ paramsError || '原样传给接口。修改参数会让用到这个模型的环节在下次运行时重新调用。' }}</div>
    <label class="k">计费</label>
    <div class="seg"><button :class="{on: !local}" @click="local = false">按 token 单价</button><button :class="{on: local}" @click="local = true">本地，不计费</button></div>
    <div class="help">模型运行在自己的电脑或服务器上时选"本地"，用量页按不计费统计。</div>
    <template v-if="!isAudio && !local">
      <label class="k">单价（每百万 token）</label>
      <div class="row wrap">
        <span class="small muted">输入</span><input class="input num" style="width:90px" v-model="price.input" placeholder="—">
        <span class="small muted">缓存输入</span><input class="input num" style="width:90px" v-model="price.cached_input" placeholder="同输入">
        <span class="small muted">输出</span><input class="input num" style="width:90px" v-model="price.output" placeholder="—">
        <select class="input" v-model="price.currency" style="width:90px"><option>USD</option><option>CNY</option><option>EUR</option></select>
      </div>
      <div class="help">可选，只用于"用量"页估算费用。修改单价不会让缓存失效。</div>
    </template>
    <label class="k">备注</label><input class="input" v-model="f.note" placeholder="可选">
  </div>
  <div class="banner" :class="test.ok ? 'ok' : 'bad'" v-if="test" style="margin:14px 0 0">
    <div class="grow" v-if="test.ok && test.usage">调用成功，用时 {{ test.ms }} 毫秒，消耗 {{ test.usage.prompt_tokens + test.usage.completion_tokens }} token。回答：{{ test.answer }}</div>
    <div class="grow" v-else-if="test.ok">连接成功</div>
    <div class="grow" v-else>{{ test.message }}</div>
  </div>
  <template #foot>
    <button class="btn" v-if="name && !copy" @click="runTest" :disabled="testing"><span class="spinner" v-if="testing"></span>测试已保存的模型</button>
    <span class="muted small" v-if="name && !copy">用已保存的设置发出一次很小的调用</span>
    <span class="spacer"></span>
    <button class="btn" @click="$emit('close')">取消</button>
    <button class="btn primary" @click="save" :disabled="!!paramsError">保存</button>
  </template>
</Modal>`,
  setup(props, { emit }) {
    const m = props.model || {};
    const params = { ...(m.params || {}) };
    const effort = ref(params.reasoning_effort || '');
    delete params.reasoning_effort;
    const f = reactive({ name: props.copy ? (props.name || '') + '-副本' : props.name || '', provider: m.provider || Object.keys(props.providers)[0] || '',
                         model: m.model || '', vision: m.vision ?? true, note: m.note || '' });
    const price = reactive({ input: m.price?.input ?? '', cached_input: m.price?.cached_input ?? '', output: m.price?.output ?? '', currency: m.price?.currency || 'USD' });
    const local = ref(!!m.price?.local);
    const paramsText = ref(Object.keys(params).length ? JSON.stringify(params) : '');
    const remote = ref([]), listing = ref(false), test = ref(null), testing = ref(false);
    const isAudio = computed(() => props.providers[f.provider]?.api === 'audio');
    const efforts = computed(() => props.providers[f.provider]?.api === 'anthropic' ? ANTHROPIC_EFFORTS : EFFORTS);
    const paramsError = computed(() => {
      if (!paramsText.value.trim()) return '';
      try { const v = JSON.parse(paramsText.value); return v && typeof v === 'object' && !Array.isArray(v) ? '' : '要写成 JSON 对象'; } catch (e) { return 'JSON 格式不对：' + e.message; }
    });
    async function listRemote() {
      listing.value = true;
      try {
        const r = await api('/api/providers/probe', { method: 'POST', body: { name: f.provider } });
        if (r.ok) remote.value = r.models; else toast(r.message, 'bad');
      } catch (e) { fail(e); }
      listing.value = false;
    }
    async function runTest() {
      testing.value = true; test.value = null;
      try { test.value = await api(`/api/models/${encodeURIComponent(props.name)}/test`, { method: 'POST', body: {} }); } catch (e) { test.value = { ok: false, message: e.message }; }
      testing.value = false;
    }
    async function save() {
      const p = paramsText.value.trim() ? JSON.parse(paramsText.value) : {};
      if (effort.value && !isAudio.value && efforts.value.some(e => e[0] === effort.value)) p.reasoning_effort = effort.value;   // one this 协议 takes
      const data = { provider: f.provider, model: f.model, params: p, vision: isAudio.value ? false : f.vision, note: f.note,
                     price: local.value ? { local: true } : isAudio.value ? {} : { input: price.input, cached_input: price.cached_input, output: price.output, currency: price.currency } };
      try {
        const r = await api('/api/models', { method: 'POST', body: { name: f.name, old_name: props.copy ? null : props.name || null, data } });
        toast(`模型 ${f.name} 已保存`, 'ok');
        emit('saved', r);
      } catch (e) { fail(e); }
    }
    return { f, price, local, effort, paramsText, paramsError, remote, listing, test, testing, isAudio, efforts, listRemote, runTest, save };
  },
};

const ADVANCED = [
  ['context_hours', '上下文小时数', '月初和月末各多取多少小时的消息，用来理解跨月的对话；这部分只作背景，不写进本月结论。', 'int'],
  ['chunk_tokens.read_a', '精读分块大小', '每次精读调用的原文上限（token）。块越大调用越少，但模型更容易漏看细节。', 'int'],
  ['chunk_tokens.scan_b', '扫读分块大小', '每次扫读调用的原文上限（token）。', 'int'],
  ['overlap_lines', '分块重叠行数', '相邻两块之间重复给出的行数，避免一段对话被切断。', 'int'],
  ['small_group_members', '小群人数上限', '人数不超过这个值的群整体精读。', 'int'],
  ['active_group.self_messages', '活跃群：我发言条数', '我当月发言达到这个条数、并且消息总数不超过下一项的群，整体精读。', 'int'],
  ['active_group.max_messages', '活跃群：消息总数上限', '', 'int'],
  ['segment_lines', '整段精读行数', '其他群里，有我参与的一段对话不超过这么多行时整段精读。', 'int'],
  ['window_lines', '精读窗口行数', '更长的对话中，围绕我的发言、@我和引用我的前后各取多少行精读。', 'int'],
  ['images.max_side', '看图：最长边像素', '图片缩小到这个尺寸再发送。', 'int'],
  ['images.max_per_month', '看图：每月上限', '超过上限时先看最重要的图片。', 'int'],
  ['images.per_call', '看图：每次几张', '', 'int'],
  ['whole_chat_a_categories', '整体精读的分类', '这些分类的会话全部精读，用逗号分隔。', 'list'],
  ['default_skip_categories', '默认整类跳过', '只在还没有保存过月报的读取范围时使用；保存过以后以月报的"读取范围"为准。', 'list'],
];
const getPath = (o, p) => p.split('.').reduce((x, k) => x?.[k], o);
function setPath(o, p, v) { const ks = p.split('.'); let x = o; ks.slice(0, -1).forEach(k => { x[k] = x[k] || {}; x = x[k]; }); x[ks[ks.length - 1]] = v; }

MRPages.settings = {
  components: { Icon, ProviderDialog, ModelDialog },
  template: `
<div class="page">
  <div class="page-head"><h1>设置</h1></div>
  <div class="tabs">
    <button v-for="t in [['models', '接口与模型'], ['general', '常规'], ['advanced', '高级'], ['config', '配置文件'], ['diagnostics', '诊断']]" :key="t[0]" :class="{on: tab === t[0]}" @click="go('settings', t[0])">{{ t[1] }}</button>
  </div>

  <template v-if="tab === 'models' && m">
    <div class="card" style="margin-bottom:14px">
      <div class="card-head"><h2>环节分配</h2><span class="muted small">每个环节用哪个模型。环节是流程的固定组成部分；修改后，受影响的环节在下次运行时重新调用。</span></div>
      <div class="card-body" style="padding-top:4px;padding-bottom:4px">
        <div class="stage" v-for="s in m.stage_defs" :key="s.key">
          <div><b>{{ s.label }}</b> <span class="chip" :class="NEEDS[s.needs][1]" v-if="s.needs !== 'text'">{{ NEEDS[s.needs][0] }}</span></div>
          <div class="desc">{{ s.desc }}</div>
          <select class="input" :value="m.stages[s.key] || ''" @change="setStage(s.key, $event.target.value)" :disabled="s.switch && !m.switches[s.switch]">
            <option value="">— 未选择 —</option>
            <option v-for="name in m.order.filter(n => !m.fits[s.key][n] || m.stages[s.key] === n)" :key="name" :value="name">{{ name }}（{{ m.models[name].model }}）</option>
          </select>
          <div>
            <label class="switch" v-if="s.switch" :title="m.switches[s.switch] ? '已开启' : '已关闭'"><input type="checkbox" :checked="m.switches[s.switch]" @change="setSwitch(s.switch, $event.target.checked)"><span></span></label>
            <span class="dot bad" v-else-if="!m.stages[s.key]" title="还没有选择模型"></span>
          </div>
        </div>
        <div class="stage" v-for="(label, i) in m.fallback_labels" :key="'fb' + i">
          <div><b>{{ label }}</b></div>
          <div class="desc">{{ i === 0 ? '任一环节多次调用仍失败时改用这个模型。看图只用支持图片的兜底，语音转写不兜底。' : '兜底一也失败时再用。' }}</div>
          <select class="input" :value="m.fallback.models[i] || ''" @change="setFallback(i, $event.target.value)" :disabled="!m.fallback.enabled">
            <option value="">— 不用 —</option>
            <option v-for="name in m.order.filter(n => m.providers[m.models[n].provider].api !== 'audio')" :key="name" :value="name">{{ name }}（{{ m.models[name].model }}）</option>
          </select>
          <div>
            <label class="switch" v-if="i === 0" :title="m.fallback.enabled ? '已开启' : '已关闭'"><input type="checkbox" :checked="m.fallback.enabled" @change="setFallbackOn($event.target.checked)"><span></span></label>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px" v-if="x">
      <div class="card-head"><h2 style="white-space:nowrap">外部 Agent</h2><span class="muted small">每次生成时选择由模型还是由你自己的 Agent 做勾选的环节；选 Agent 时用它的额度，没勾选的环节照常走接口。要用到结果而还没交回时，该月停下来等，交齐后自动继续。</span></div>
      <div class="card-body form-card" style="padding-top:4px;padding-bottom:10px">
        <div class="setting"><div><div class="k">交给外部 Agent 的环节</div><div class="help">这些环节仍要在上面选好模型：抽查和"改用模型做完"时用它。</div></div>
          <div class="ctl"><label class="check" v-for="s in store.settings.external.stages" :key="s.key"><input type="checkbox" :value="s.key" v-model="x.external_stages">{{ s.label }}</label></div><div></div></div>
        <div class="setting"><div><div class="k">交接文件夹</div><div class="help">选择交给外部 Agent 生成时，这些环节的任务写进这里，每个环节一个子文件夹；第一次写出任务时才建立。任务写出后，在月报页该月份的"运行"里（专题在专题页）生成提示词、复制给 Agent。</div></div>
          <div class="ctl"><input class="input grow" v-model="x.external_root" :placeholder="'留空：' + store.settings.external.default_root"><button class="btn" v-if="canPick()" @click="pickExternal"><Icon name="folder"/>选择</button>
            <button class="btn ghost" v-if="store.settings.external.root_exists && x.external_root.trim() === (store.settings.settings.external_root || '')" @click="openPath(store.settings.external.root)">打开</button></div><div></div></div>
        <div class="setting"><div><div class="k">抽查比例</div><div class="help">收回结果时随机抽这个比例，让该环节的模型再做一遍来对比；外部结果漏掉要点的那些改用模型的结果，抽查情况写进报告附录。填 0 不抽查。</div></div>
          <div class="ctl"><input class="input num" type="number" min="0" max="100" style="width:90px" v-model.number="x.external_audit"><span>%</span></div><div></div></div>
        <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="btn" @click="loadExternal">还原</button><button class="btn primary" @click="saveExternal">保存</button></div>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px">
      <div class="card-head"><h2>接口</h2><span class="muted small">模型从哪里调用。密钥保存在 Windows 凭据管理器中，不写进配置文件。</span><span class="spacer"></span>
        <button class="btn sm primary" @click="dlg = {kind: 'provider'}"><Icon name="plus"/>添加接口</button></div>
      <table class="t" v-if="Object.keys(m.providers).length">
        <thead><tr><th>名称</th><th>地址</th><th>协议</th><th>密钥</th><th>状态</th><th></th></tr></thead>
        <tbody><tr v-for="(p, name) in m.providers" :key="name">
          <td><b>{{ name }}</b><div class="muted small">{{ p.label }}</div></td><td class="mono">{{ p.base_url }}</td><td class="small">{{ m.apis[p.api] }}</td>
          <td><span class="chip" :class="{saved: 'ok', missing: 'bad'}[p.key_state] || ''">{{ keyText(p) }}</span></td>
          <td class="small"><template v-if="probes[name]"><span v-if="probes[name] === 'wait'" class="spinner"></span>
            <span v-else :style="{color: probes[name].ok ? 'var(--ok)' : 'var(--bad)'}">{{ probes[name].ok ? '正常 · ' + probes[name].models.length + ' 个模型' : probes[name].message }}</span></template></td>
          <td class="r" style="white-space:nowrap"><button class="btn sm" @click="probe(name)">测试</button>
            <button class="btn ghost sm icon" title="编辑" @click="dlg = {kind: 'provider', name, provider: p}"><Icon name="edit"/></button>
            <button class="btn ghost sm icon danger" title="删除" @click="delProvider(name)"><Icon name="trash"/></button></td>
        </tr></tbody>
      </table>
      <div class="empty" v-else>还没有接口。先添加一个接口，再添加它提供的模型。</div>
    </div>

    <div class="card">
      <div class="card-head"><h2>模型</h2><span class="spacer"></span>
        <button class="btn sm primary" @click="dlg = {kind: 'model'}" :disabled="!Object.keys(m.providers).length"><Icon name="plus"/>添加模型</button></div>
      <table class="t" v-if="Object.keys(m.models).length">
        <thead><tr><th>名称</th><th>接口</th><th>模型名</th><th>参数</th><th>图片</th><th>单价（每百万）</th><th>使用的环节</th><th></th></tr></thead>
        <tbody><tr v-for="({name, spec}, i) in m.order.map(n => ({name: n, spec: m.models[n]}))" :key="name">
          <td><b>{{ name }}</b><div class="muted small" v-if="spec.note">{{ spec.note }}</div></td><td class="small">{{ spec.provider }}</td><td class="mono">{{ spec.model }}</td>
          <td class="mono small">{{ Object.keys(spec.params || {}).length ? JSON.stringify(spec.params) : '' }}</td>
          <td>{{ spec.vision ? '✓' : '' }}</td>
          <td class="small num" style="white-space:nowrap">{{ priceText(spec.price) }}</td>
          <td class="small">{{ usedBy(name) || '—' }}</td>
          <td class="r" style="white-space:nowrap">
            <button class="btn ghost sm icon" title="上移" :disabled="i === 0" @click="moveModel(name, -1)"><Icon name="up"/></button>
            <button class="btn ghost sm icon" title="下移" :disabled="i === m.order.length - 1" @click="moveModel(name, 1)"><Icon name="down"/></button>
            <button class="btn ghost sm icon" title="编辑" @click="dlg = {kind: 'model', name, model: spec}"><Icon name="edit"/></button>
            <button class="btn ghost sm icon" title="复制为新模型" @click="dlg = {kind: 'model', name, model: spec, copy: true}"><Icon name="copy"/></button>
            <button class="btn ghost sm icon danger" title="删除" @click="delModel(name)"><Icon name="trash"/></button></td>
        </tr></tbody>
      </table>
      <div class="empty" v-else>还没有模型。</div>
    </div>
    <ProviderDialog v-if="dlg && dlg.kind === 'provider'" :name="dlg.name" :provider="dlg.provider" :apis="m.apis" @close="dlg = null" @saved="r => { store.models = r; dlg = null }"/>
    <ModelDialog v-if="dlg && dlg.kind === 'model'" :name="dlg.name" :model="dlg.model" :copy="dlg.copy" :providers="m.providers" @close="dlg = null" @saved="r => { store.models = r; dlg = null }"/>
  </template>

  <template v-if="tab === 'general' && g">
    <div class="card pad form-card">
      <div class="setting" v-if="zoom !== null"><div><div class="k">界面大小</div><div class="help">立即生效，下次打开时保持。也可以按住 Ctrl 滚动鼠标滚轮，或按 Ctrl+加号 / 减号，Ctrl+0 恢复 100%。</div></div>
        <div class="ctl"><div class="seg"><button v-for="z in ZOOMS" :key="z" :class="{on: Math.abs(zoom - z) < 0.01}" @click="setZoom(z)">{{ Math.round(z * 100) }}%</button></div>
          <span class="muted small" v-if="!ZOOMS.some(z => Math.abs(zoom - z) < 0.01)">当前 {{ Math.round(zoom * 100) }}%</span></div><div></div></div>
      <div class="setting"><div><div class="k">归档目录</div><div class="help">聊天记录归档所在的文件夹。程序读取其中的聊天记录 Markdown，不修改它们；只在转写语音时，把文字存成语音文件旁边的同名 .txt。</div></div>
        <div class="ctl"><input class="input grow" v-model="g.archive_root"><button class="btn" v-if="canPick()" @click="pick('archive_root')"><Icon name="folder"/>选择</button></div><div></div></div>
      <div class="setting"><div><div class="k">输出目录</div><div class="help">月报、专题、缓存和用量记录放在这里。月报引用聊天内容，不建议选会被网盘同步的文件夹。</div></div>
        <div class="ctl"><input class="input grow" v-model="g.output_root"><button class="btn" v-if="canPick()" @click="pick('output_root')"><Icon name="folder"/>选择</button>
          <button class="btn ghost" @click="openPath(g.output_root)">打开</button></div><div></div></div>
      <div class="setting"><div><div class="k">每月 token 预算</div><div class="help">每个月份累计用到这个值时自动暂停，可以在运行页追加预算继续。用量页会根据最近几期给出建议值。</div></div>
        <div class="ctl"><div class="seg"><button :class="{on: !budgetOn}" @click="budgetOn = false">不限</button><button :class="{on: budgetOn}" @click="budgetOn = true">指定</button></div>
          <template v-if="budgetOn"><input class="input num" style="width:110px" v-model="budgetM"><span>M（百万 token）</span></template></div><div></div></div>
      <div class="setting"><div><div class="k">并发数</div><div class="help">同时发出的调用数。太高可能触发接口限流。</div></div>
        <div class="ctl"><input class="input num" type="number" min="1" max="32" style="width:90px" v-model.number="g.concurrency"></div><div></div></div>
      <div class="setting"><div><div class="k">完成后通知</div><div class="help">月报完成、失败或到达预算上限时，发出 Windows 通知。</div></div>
        <div class="ctl"><label class="switch"><input type="checkbox" v-model="g.notify"><span></span></label></div><div></div></div>
      <div class="setting"><div><div class="k">Markdown 查看器</div><div class="help">打开月报文件和"在归档中打开"时用这个程序，并跳到那条消息（命令行：程序 "文件#锚点"）。留空则用系统默认程序打开。</div></div>
        <div class="ctl"><input class="input grow" v-model="g.markdown_viewer" placeholder="留空：系统默认程序"><button class="btn" v-if="canPick()" @click="pickExe"><Icon name="folder"/>选择</button></div><div></div></div>
      <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="btn" @click="loadGeneral">还原</button><button class="btn primary" @click="saveGeneral">保存</button></div>
    </div>
  </template>

  <template v-if="tab === 'advanced' && adv">
    <div class="banner info"><Icon name="info"/><div class="grow">这些设置决定读什么、怎样读。修改以后只影响之后生成的月报，已经生成的月报保持不变。</div></div>
    <div class="card pad form-card">
      <div class="setting" v-for="a in ADVANCED" :key="a[0]">
        <div><div class="k">{{ a[1] }}</div><div class="help">{{ a[2] }}</div></div>
        <div class="ctl">
          <input v-if="a[3] === 'int'" class="input num" type="number" style="width:120px" v-model.number="adv[a[0]]">
          <input v-else class="input grow" v-model="adv[a[0]]">
          <span class="muted small" v-if="String(adv[a[0]]) !== String(defaultOf(a))">默认 {{ defaultOf(a) }}</span>
        </div>
        <div><button class="btn ghost sm" v-if="String(adv[a[0]]) !== String(defaultOf(a))" @click="adv[a[0]] = defaultOf(a)">恢复默认</button></div>
      </div>
      <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="btn" @click="loadAdvanced">还原</button><button class="btn primary" @click="saveAdvanced">保存</button></div>
    </div>
  </template>

  <template v-if="tab === 'config' && cfg">
    <div class="card pad form-card">
      <div class="setting"><div><div class="k">配置目录</div><div class="help">接口与模型、各环节用的模型、常规和高级设置、读取范围、月报里的勾选都在这里。程序升级或重装不会动它。</div></div>
        <div class="ctl"><span class="mono grow">{{ cfg.home }}</span><button class="btn ghost" @click="openPath(cfg.home)">打开</button></div><div></div></div>
      <div class="setting"><div><div class="k">导出</div><div class="help">把上面这些打包成一个 .zip。装到另一处或另一台电脑以后，在那里导入它，就不用重新配置。</div></div>
        <div class="ctl"><input class="input grow" v-model="exportPath"><button class="btn" v-if="canSave()" @click="pickExport"><Icon name="folder"/>选择</button>
          <button class="btn primary" @click="doExport" :disabled="busy || !exportPath.trim()">导出</button></div><div></div></div>
      <div class="setting"><div><div class="k">导入</div><div class="help">导出的 .zip，或者另一份配置目录。导入的内容替换当前配置，被替换的文件先备份到配置目录的"导入前备份"里。</div></div>
        <div class="ctl"><input class="input grow" v-model="importPath" placeholder=".zip 文件或配置目录的位置">
          <button class="btn" v-if="canSave()" @click="pickImport('zip')"><Icon name="folder"/>选择文件</button>
          <button class="btn" v-if="canPick()" @click="pickImport('dir')"><Icon name="folder"/>选择目录</button>
          <button class="btn primary" @click="doImport" :disabled="busy || !importPath.trim()">导入</button></div><div></div></div>
    </div>
    <div class="banner info" style="margin-top:14px"><Icon name="info"/><div class="grow">密钥不在配置文件里。它们保存在 Windows 凭据管理器中，同一台电脑上导入后可以直接使用；换到另一台电脑，要在"接口与模型"里重新填写密钥。月报、专题、用量记录和语音转写在输出目录中，不随配置导出。</div></div>
  </template>

  <template v-if="tab === 'diagnostics'">
    <div class="card pad" v-if="diag" style="margin-bottom:14px">
      <table class="t"><tbody>
        <tr><td style="width:200px">版本</td><td>{{ diag.version }}（Python {{ diag.python }}）</td><td></td></tr>
        <tr><td>程序目录</td><td class="mono">{{ diag.program }}</td><td class="r"><button class="btn sm" @click="openDir(diag.program)">打开</button></td></tr>
        <tr><td>配置目录</td><td class="mono">{{ diag.home }}</td><td class="r"><button class="btn sm" @click="openPath(diag.home)">打开</button></td></tr>
        <tr><td>输出目录</td><td class="mono">{{ diag.output_root }}</td><td class="r"><button class="btn sm" @click="openPath(diag.output_root)">打开</button></td></tr>
        <tr><td>归档目录</td><td><span :style="{color: diag.archive.ok ? 'var(--ok)' : 'var(--bad)'}">{{ diag.archive.ok ? '正常' : '找不到' }}</span> <span class="mono">{{ diag.archive.path }}</span>
          <div class="small muted" v-if="diag.archive.cutoff">{{ diag.archive.chats }} 个会话 · 最新消息 {{ diag.archive.cutoff }}</div>
          <div class="small muted" v-else-if="diag.archive.problem">{{ diag.archive.problem }}</div></td><td></td></tr>
        <tr><td>密钥存储</td><td>{{ diag.keyring }}</td><td></td></tr>
        <tr><td>界面日志</td><td class="mono">{{ diag.home }}\\logs</td><td class="r"><button class="btn sm" @click="openPath(diag.home + '\\\\logs')">打开</button></td></tr>
      </tbody></table>
    </div>
    <div class="card">
      <div class="card-head"><h2>接口连通</h2><span class="spacer"></span><button class="btn sm" @click="probeAll" :disabled="probing"><span class="spinner" v-if="probing"></span>测试全部</button></div>
      <table class="t" v-if="diag"><tbody><tr v-for="p in diag.providers" :key="p.name">
        <td style="width:200px"><b>{{ p.name }}</b></td><td class="mono">{{ p.base_url }}</td>
        <td><template v-if="allProbes"><span :style="{color: allProbes[p.name].ok ? 'var(--ok)' : 'var(--bad)'}">{{ allProbes[p.name].ok ? '正常（' + allProbes[p.name].ms + ' 毫秒）' : allProbes[p.name].message }}</span></template></td>
      </tr></tbody></table>
    </div>
  </template>
</div>`,
  setup() {
    const tab = computed(() => route.arg || 'models');
    const m = computed(() => store.models);
    const dlg = ref(null), probes = reactive({}), g = ref(null), adv = ref(null), budgetOn = ref(false), budgetM = ref('');
    const diag = ref(null), allProbes = ref(null), probing = ref(false);
    const cfg = ref(null), exportPath = ref(''), importPath = ref(''), busy = ref(false);
    const canSave = () => !!window.pywebview?.api?.pick_save;
    async function loadConfig() { try { cfg.value = await api('/api/config'); if (!exportPath.value) exportPath.value = cfg.value.export_path; if (!importPath.value) importPath.value = cfg.value.import_path; } catch (e) { fail(e); } }
    async function pickExport() { const p = await window.pywebview.api.pick_save(exportPath.value, 'zip'); if (p) exportPath.value = p; }
    async function pickImport(kind) { const p = kind === 'dir' ? await pickFolder('') : await window.pywebview.api.pick_open('zip'); if (p) importPath.value = p; }
    async function doExport() {
      busy.value = true;
      try { const r = await api('/api/config/export', { method: 'POST', body: { path: exportPath.value } }); toast(`已导出到 ${r.path}`, 'ok'); } catch (e) { fail(e); }
      busy.value = false;
    }
    async function doImport() {
      if (!await confirmBox('导入配置', `用"${importPath.value}"中的配置替换当前配置？

被替换的文件先备份到配置目录的"导入前备份"里。导入以后界面会重新载入。`, { ok: '导入' })) return;
      busy.value = true;
      try {
        const r = await api('/api/config/import', { method: 'POST', body: { path: importPath.value } });
        toast('已导入配置' + (r.backup ? '，原来的配置备份在 ' + r.backup : ''), 'ok');
        setTimeout(() => location.reload(), 1200);
      } catch (e) { fail(e); busy.value = false; }
    }
    const defaults = computed(() => store.settings?.defaults || {});
    const x = ref(null);
    async function loadExternal() {
      const s = (await loadSettings()).settings;
      x.value = { external_stages: [...s.external_stages], external_root: s.external_root || '', external_audit: s.external_audit };
    }
    async function saveExternal() {
      if (!Number.isInteger(x.value.external_audit) || x.value.external_audit < 0 || x.value.external_audit > 100) return toast('抽查比例请填 0 到 100 的整数', 'warn');
      try { await api('/api/settings', { method: 'PUT', body: { ...x.value, external_root: x.value.external_root.trim() } }); toast('已保存', 'ok'); loadExternal(); } catch (e) { fail(e); }
    }
    async function pickExternal() { const p = await pickFolder(x.value.external_root || store.settings.external.default_root); if (p) x.value.external_root = p; }
    async function setFallback(i, name) {
      const models = m.value.fallback.models.slice();
      models[i] = name;
      try { store.models = await api('/api/stages', { method: 'PUT', body: { fallback: { models } } }); toast('已保存兜底模型', 'ok'); }
      catch (e) { fail(e); store.models = { ...store.models }; }
    }
    async function setFallbackOn(on) {
      try { store.models = await api('/api/stages', { method: 'PUT', body: { fallback: { enabled: on } } }); toast(on ? '已开启兜底' : '已关闭兜底', 'ok'); }
      catch (e) { fail(e); }
    }
    async function setStage(stage, name) {
      try { store.models = await api('/api/stages', { method: 'PUT', body: { stages: { [stage]: name || null } } }); toast('已保存环节分配', 'ok'); }
      catch (e) { fail(e); loadModels(); }
    }
    async function setSwitch(sw, on) {
      try { store.models = await api('/api/stages', { method: 'PUT', body: { switches: { [sw]: on } } }); toast(on ? '已开启' : '已关闭', 'ok'); loadOverview(); }
      catch (e) { fail(e); }
    }
    async function probe(name) {
      probes[name] = 'wait';
      try { probes[name] = await api('/api/providers/probe', { method: 'POST', body: { name } }); } catch (e) { probes[name] = { ok: false, message: e.message }; }
    }
    async function delProvider(name) {
      if (!await confirmBox('删除接口', `删除接口"${name}"？保存在凭据管理器中的密钥也会一起删除。`, { ok: '删除', danger: true })) return;
      try { store.models = await api(`/api/providers/${encodeURIComponent(name)}`, { method: 'DELETE' }); } catch (e) { fail(e); }
    }
    async function delModel(name) {
      if (!await confirmBox('删除模型', `删除模型"${name}"？`, { ok: '删除', danger: true })) return;
      try { store.models = await api(`/api/models/${encodeURIComponent(name)}`, { method: 'DELETE' }); } catch (e) { fail(e); }
    }
    async function moveModel(name, by) {
      try { store.models = await api(`/api/models/${encodeURIComponent(name)}/move`, { method: 'POST', body: { by } }); } catch (e) { fail(e); }
    }
    const usedBy = name => (m.value?.stage_defs || []).filter(s => m.value.stages[s.key] === name).map(s => s.label)
      .concat((m.value?.fallback_labels || []).filter((l, i) => m.value.fallback.models[i] === name)).join('、');
    const keyText = p => ({ none: '不需要', saved: '已保存', missing: '未填写' })[p.key_state];
    function priceText(p) {
      if (p?.local) return '本地';
      if (!p || p.input === undefined) return '未定价';
      const s = { USD: '$', CNY: '¥', EUR: '€' }[p.currency] || '';
      return `入 ${s}${p.input}` + (p.cached_input !== undefined ? ` · 缓存 ${s}${p.cached_input}` : '') + ` · 出 ${s}${p.output ?? '—'}`;
    }
    async function loadGeneral() {
      const r = await loadSettings();
      const s = r.settings;
      g.value = { archive_root: s.archive_root, output_root: s.output_root, concurrency: s.concurrency, notify: s.notify, markdown_viewer: s.markdown_viewer || '' };
      budgetOn.value = !!s.budget_tokens; budgetM.value = s.budget_tokens ? String(s.budget_tokens / 1e6) : '';
    }
    async function saveGeneral() {
      const body = { ...g.value };
      if (budgetOn.value) {
        const v = parseFloat(budgetM.value);
        if (!(v > 0)) return toast('预算请填写大于 0 的数字（单位：百万 token）', 'warn');
        body.budget_tokens = Math.round(v * 1e6);
      } else body.budget_tokens = null;
      try { await api('/api/settings', { method: 'PUT', body }); toast('已保存', 'ok'); loadGeneral(); loadOverview(); } catch (e) { fail(e); }
    }
    const defaultOf = a => { const v = getPath(defaults.value, a[0]); return Array.isArray(v) ? v.join(', ') : v; };
    async function loadAdvanced() {
      const r = await loadSettings();
      adv.value = Object.fromEntries(ADVANCED.map(a => { const v = getPath(r.settings, a[0]); return [a[0], Array.isArray(v) ? v.join(', ') : v]; }));
    }
    async function saveAdvanced() {
      const body = {};
      for (const a of ADVANCED) {
        let v = adv.value[a[0]];
        if (a[3] === 'int') { if (!Number.isInteger(v) || v < 0) return toast(`"${a[1]}"要填非负整数`, 'warn'); }
        else v = String(v).split(/[,，、\s]+/).map(x => x.trim()).filter(Boolean);
        setPath(body, a[0], v);
      }
      try { await api('/api/settings', { method: 'PUT', body }); toast('已保存，之后生成的月报会按新设置。', 'ok'); loadAdvanced(); loadOverview(); } catch (e) { fail(e); }
    }
    async function pick(key) { const p = await pickFolder(g.value[key]); if (p) g.value[key] = p; }
    // Window zoom lives in the program window (WebView2), not in the settings file; absent in a plain browser.
    const ZOOMS = [0.9, 1, 1.1, 1.25, 1.5];
    const zoom = ref(null);
    async function loadZoom() { if (window.pywebview?.api?.get_zoom) zoom.value = await window.pywebview.api.get_zoom(); }
    async function setZoom(z) { try { zoom.value = await window.pywebview.api.set_zoom(z); } catch (e) { fail(e); } }
    window.addEventListener('pywebviewready', loadZoom);
    onBeforeUnmount(() => window.removeEventListener('pywebviewready', loadZoom));
    async function pickExe() { const p = await pickFile(g.value.markdown_viewer); if (p) g.value.markdown_viewer = p; }
    async function loadDiag() { try { diag.value = await api('/api/diagnostics'); } catch (e) { fail(e); } }
    async function probeAll() {
      probing.value = true;
      try { allProbes.value = await api('/api/diagnostics/providers', { method: 'POST', body: {} }); } catch (e) { fail(e); }
      probing.value = false;
    }
    function openDir(p) { openPath(p + '\\README.md', true); }
    watch(tab, t => {
      if (t === 'models') { loadModels().catch(fail); loadExternal().catch(fail); }
      if (t === 'general') { loadGeneral().catch(fail); loadZoom(); }
      if (t === 'advanced') loadAdvanced().catch(fail);
      if (t === 'config') loadConfig();
      if (t === 'diagnostics') loadDiag();
    }, { immediate: true });
    return { tab, m, dlg, probes, g, adv, budgetOn, budgetM, diag, allProbes, probing, store, NEEDS, ADVANCED,
             setStage, setSwitch, setFallback, setFallbackOn, probe, delProvider, delModel, moveModel, usedBy, keyText, priceText, loadGeneral, saveGeneral, defaultOf,
             loadAdvanced, saveAdvanced, pick, pickExe, canPick, canSave, ZOOMS, zoom, setZoom, probeAll, openPath, openDir, go,
             cfg, exportPath, importPath, busy, pickExport, pickImport, doExport, doImport, x, loadExternal, saveExternal, pickExternal };
  },
};
