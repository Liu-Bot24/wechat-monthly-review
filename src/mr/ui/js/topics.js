/* 专题: one-off summaries over chats, people, dates and a topic I choose; kept apart from the monthly reports. */
const pad2 = n => String(n).padStart(2, '0');
function monthEnd(ym) { const [y, m] = ym.split('-').map(Number); return `${ym}-${pad2(new Date(y, m, 0).getDate())}`; }
function rangeText(r) {
  if (!r || !r.from || !r.to) return '未选时间';
  if (r.from === r.to) return r.from;
  if (r.unit === 'month' && r.to === monthEnd(r.from.slice(0, 7))) return monthLabel(r.from.slice(0, 7));
  if (r.unit === 'year' && r.from.endsWith('-01-01') && r.to === r.from.slice(0, 4) + '-12-31') return r.from.slice(0, 4) + ' 年';
  return `${r.from} 至 ${r.to}`;
}
const TOPIC_JOB = { 'topic-plan': '预览', 'topic-screen': '模型筛查', 'topic-run': '生成' };

// A search box that picks a person (friends and group members), or from candidates the page gives (source).
const Picker = {
  props: { kind: String, placeholder: String, source: Function },
  emits: ['pick'],
  template: `
<div class="picker">
  <input class="input sm" v-model="q" :placeholder="placeholder" @focus="open = true" @blur="open = false"
         @keydown.down.prevent="cur = Math.min(cur + 1, results.length - 1)" @keydown.up.prevent="cur = Math.max(cur - 1, 0)"
         @keydown.enter.prevent="choose(results[cur])" @keydown.esc="q = ''">
  <div class="picker-list" v-if="open && q.trim()">
    <div v-for="(r, i) in results" :key="r.id" class="row" :class="{on: i === cur}" @mousedown.prevent="choose(r)" @mouseenter="cur = i">
      <span class="grow ellipsis">{{ r.name }}</span><span class="muted small">{{ r.category === '群成员' ? r.category : catTitle(r.category) }}</span></div>
    <div v-if="!results.length" class="muted small" style="padding:6px 10px">{{ loading ? '正在查找…' : '没有找到' }}</div>
  </div>
</div>`,
  setup(props, { emit }) {
    const q = ref(''), results = ref([]), cur = ref(0), open = ref(false), loading = ref(false);
    let timer = null, seq = 0;
    watch(q, v => { cur.value = 0; clearTimeout(timer); timer = setTimeout(() => search(v), props.source ? 60 : 250); });
    async function search(v) {
      v = v.trim();
      const n = ++seq;
      if (!v) { results.value = []; return; }
      loading.value = true;
      try {
        let found;
        found = props.source ? props.source(v) : (await api('/api/topics/people?q=' + encodeURIComponent(v))).people;
        if (n === seq) results.value = found;
      } catch (e) { fail(e); }
      loading.value = false;
    }
    function choose(r) { if (!r) return; emit('pick', r); q.value = ''; results.value = []; }
    return { q, results, cur, open, loading, choose, catTitle };
  },
};

MRPages.topics = {
  components: { Icon, Bar, Modal, ProgressView, ContextView, MaskModal, Picker, ExternalWait },
  template: `
<div class="page full">
  <div class="topics" :class="{side: !!lid}">
    <div class="rlist">
      <div class="row" style="padding:4px 4px 8px 10px"><span class="muted small grow">专题</span>
        <button class="btn sm primary" @click="create"><Icon name="plus"/>新建</button></div>
      <a v-for="t in list" :key="t.id" :href="'#/topics/' + t.id" :class="{on: t.id === id}" class="titem">
        <span class="grow" style="min-width:0">
          <span class="ellipsis" style="display:block">{{ t.title || '未命名专题' }}</span>
          <span class="muted small ellipsis" style="display:block;font-weight:400">{{ rangeText(t.range) }}{{ t.job ? ' · ' + (t.job === 'queued' ? '排队中' : '运行中') : t.report ? '' : ' · 还没有生成' }}</span>
        </span>
        <span class="dot" :class="t.job ? 'run' : stateKind(t.state) === 'ok' ? 'ok' : stateKind(t.state) ? 'warn' : ''"></span></a>
      <div class="empty small" v-if="!list.length" style="text-align:left">还没有专题。专题是一次单独的总结：任选会话、人、时间和话题，结果和月报分开保存。</div>
    </div>

    <div class="rmain">
      <div v-if="!id" class="empty" style="margin-top:12vh;line-height:2">
        <div style="font-size:16px;font-weight:600;color:var(--text)">单独总结一段聊天</div>
        <div>任选会话、人、时间和话题，按你要的视角和模板写成一份总结。<br>结果放在输出目录的"专题"文件夹里，和月报分开。</div>
        <button class="btn primary" style="margin-top:12px" @click="create"><Icon name="plus"/>新建专题</button>
      </div>
      <div v-else-if="!detail" class="empty"><span class="spinner"></span></div>
      <template v-else>
        <div class="rtools">
          <input class="input title-input" v-model="spec.title" placeholder="专题标题">
          <span class="muted small">{{ saving }}</span>
          <span class="spacer"></span>
          <button class="btn sm" @click="openScope" title="这个专题读哪些会话"><Icon name="chats"/>读取范围</button>
          <button class="btn sm" @click="startJob('topic-plan')" :disabled="busy" title="取数并查找，不调用模型"><Icon name="search"/>预览</button>
          <button class="btn sm" v-if="spec.topic.mode === 'ai'" @click="startJob('topic-screen')" :disabled="busy" title="让模型读一遍范围内的消息，找出和话题有关的部分，先不总结"><Icon name="spark"/>模型筛查</button>
          <button class="btn sm primary" @click="startJob('topic-run')" :disabled="busy"><Icon name="play"/>{{ repairable ? '补做失败的部分' : report ? '重新生成' : '生成' }}</button>
          <button class="btn ghost sm icon" title="另存为新专题" @click="duplicate"><Icon name="copy"/></button>
          <button class="btn ghost sm icon" title="删除这个专题" @click="remove"><Icon name="trash"/></button>
        </div>
        <div class="tabs" style="margin:0;padding:0 12px;background:var(--panel)">
          <button :class="{on: tab === 'config'}" @click="tab = 'config'">配置</button>
          <button :class="{on: tab === 'preview'}" @click="tab = 'preview'">匹配预览<span class="muted small" v-if="plan"> {{ planStale ? '（已过期）' : '' }}</span></button>
          <button :class="{on: tab === 'result'}" @click="tab = 'result'">结果<span class="muted small" v-if="report && reportStale"> （配置改过）</span></button>
        </div>
        <div class="rscroll" ref="scroller">
          <div class="tpad">
            <div class="card" style="margin-bottom:14px" v-if="job">
              <div class="card-head"><span class="dot run"></span><h2>正在{{ TOPIC_JOB[job.kind] }}</h2>
                <span class="spacer"></span>
                <button class="btn sm" @click="showLog(job.log)"><Icon name="log"/>日志</button>
                <button class="btn sm danger" @click="stop"><Icon name="stop"/>停止</button></div>
              <div class="card-body">
                <ProgressView v-if="job.progress" :p="job.progress" live/>
                <div v-else class="row muted"><span class="spinner"></span>{{ job.kind === 'topic-plan' ? '正在取数和查找（不调用模型）…' : '正在启动…' }}</div>
              </div>
            </div>
            <div class="banner warn" v-else-if="queued"><Icon name="info"/><div class="grow">这个专题的任务在排队，前面的任务完成后开始。</div>
              <button class="btn sm" @click="go('run')">查看队列</button></div>
            <div class="banner bad" v-if="!job && lastFail"><Icon name="warn"/><div class="grow">上次{{ lastFail.what }}没有完成：{{ lastFail.message }}</div>
              <button class="btn sm" v-if="lastFail.log" @click="showLog(lastFail.log)"><Icon name="log"/>日志</button></div>
            <ExternalWait v-if="handoff" :h="handoff" :message="'这个专题有 ' + handoff.tasks + ' 个任务由外部 Agent 做。' + (job ? '接口这边照常进行；要用到 Agent 的结果而它还没交回时停下来等，交齐后自动继续。' : detail.status && detail.status.state === 'external-wait' ? '现在在等它，结果交齐后自动继续（消息月报的窗口开着时）。' : '再生成时会用上已经交回的结果。')">
              <button class="btn sm" v-if="!job" :disabled="busy" title="不等 Agent：还没有结果的部分由各环节的模型完成，按接口计费" @click="finish(true)">改用模型做完</button>
            </ExternalWait>
            <div class="banner warn" v-if="detail.missing.length"><Icon name="warn"/>
              <div class="grow">专题用的是月报各环节的模型，这些环节还没有选择：{{ detail.missing.join('、') }}。</div>
              <button class="btn sm" @click="go('settings', 'models')">去设置</button></div>

            <!-- ===== 配置 ===== -->
            <div v-show="tab === 'config'" class="tconfig">
              <div class="col" style="gap:14px">
                <div class="card">
                  <div class="card-head"><h2>想总结什么</h2><span class="muted small">用一句话说，模型拆成下面的配置，你再改</span></div>
                  <div class="card-body">
                    <textarea class="input" ref="ask" v-model="spec.request" rows="4" style="width:100%;resize:vertical"
                      placeholder="要读哪些聊天、什么时间、关心什么话题、写成什么样、给谁看" @keydown.ctrl.enter="draft"></textarea>
                    <div class="row" style="margin-top:8px">
                      <button class="btn primary" @click="draft" :disabled="drafting || !spec.request.trim()"><Icon name="spark"/>{{ drafting ? '正在拆解…' : '拆解成配置' }}</button>
                      <span class="muted small">Ctrl+Enter。会把这段话和会话分类名发给"总览成稿"环节的模型；会话和人名在本机匹配。</span>
                    </div>
                    <div class="banner info" style="margin:12px 0 0" v-if="spec.understanding"><Icon name="info"/><div class="grow">{{ spec.understanding }}</div></div>
                    <div class="banner warn" style="margin:12px 0 0" v-for="(p, i) in problems" :key="i"><Icon name="warn"/>
                      <div class="grow">
                        <div>{{ p.candidates.length ? '"' + p.name + '"对应多个' + (p.kind === 'chats' ? '会话' : '人') + '，选一个：' : '没有找到"' + p.name + '"，可以在下面手动添加。' }}</div>
                        <div class="row wrap" style="gap:6px;margin-top:6px" v-if="p.candidates.length">
                          <button class="btn sm" v-for="c in p.candidates" :key="c.id" @click="settle(p, c)">{{ c.name }} <span class="muted small">{{ c.category }}</span></button></div>
                      </div>
                      <button class="btn ghost sm icon" title="忽略" @click="problems.splice(i, 1)"><Icon name="x"/></button>
                    </div>
                  </div>
                </div>

                <div class="card">
                  <div class="card-head"><h2>读什么</h2></div>
                  <div class="card-body form">
                    <label class="k">时间</label>
                    <div class="row wrap">
                      <div class="seg">
                        <button v-for="u in UNITS" :key="u[0]" :class="{on: spec.range.unit === u[0]}" @click="setUnit(u[0])">{{ u[1] }}</button>
                      </div>
                      <input v-if="spec.range.unit === 'day'" type="date" class="input sm" :value="spec.range.from" :max="meta && meta.last_day" @change="e => setDay(e.target.value)">
                      <input v-if="spec.range.unit === 'month'" type="month" class="input sm" :value="spec.range.from.slice(0, 7)" @change="e => setMonth(e.target.value)">
                      <input v-if="spec.range.unit === 'year'" type="number" class="input sm" style="width:90px" min="2000" max="2100" :value="spec.range.from.slice(0, 4)" @change="e => setYear(e.target.value)">
                      <template v-if="spec.range.unit === 'custom'">
                        <input type="date" class="input sm" v-model="spec.range.from"><span class="muted">至</span><input type="date" class="input sm" v-model="spec.range.to">
                      </template>
                      <span class="muted small">{{ spec.range.from && spec.range.to ? spec.range.from + ' 至 ' + spec.range.to : '' }}</span>
                    </div>
                    <div class="help" v-if="meta">归档数据到 {{ meta.last_day }}</div>

                    <label class="k">范围</label>
                    <div class="col" style="gap:8px;align-items:stretch">
                      <label class="row" style="cursor:pointer;align-self:flex-start"><input type="checkbox" v-model="spec.scope.follow_monthly">沿用月报的读取范围</label>
                      <div class="scope-sum">
                        <div class="grow" style="min-width:0">
                          <div>{{ detail.scope ? detail.scope.text : '' }}</div>
                          <div class="muted small" v-if="detail.scope">{{ detail.scope.in_range ? '所选时间里有消息的会话，读 ' + fmtInt(detail.scope.chats) + ' 个' : '读 ' + fmtInt(detail.scope.chats) + ' 个会话' }}{{ detail.scope.people ? '，联系人 ' + detail.scope.people + ' 人在群聊里的发言' : '' }}</div>
                        </div>
                        <button class="btn sm" @click="openScope"><Icon name="chats"/>{{ spec.scope.follow_monthly ? '查看月报的读取范围' : '选择会话' }}</button>
                      </div>
                    </div>

                    <label class="k">话题</label>
                    <div class="col" style="gap:8px;align-items:stretch">
                      <div class="seg" style="align-self:flex-start">
                        <button v-for="(v, k) in meta ? meta.modes : {}" :key="k" :class="{on: spec.topic.mode === k}" @click="spec.topic.mode = k">{{ v }}</button></div>
                      <template v-if="spec.topic.mode !== 'none'">
                        <input class="input" v-model="spec.topic.description" placeholder="话题：一句话说明要找什么">
                        <div class="row wrap kw" style="gap:5px">
                          <span v-for="(k, i) in spec.topic.keywords" :key="k" class="chip">{{ k }}<a href="" @click.prevent="spec.topic.keywords.splice(i, 1)" title="去掉">×</a></span>
                          <input class="input sm" style="width:200px" v-model="kwText" placeholder="添加关键词，回车确认" @keydown.enter.prevent="addKeywords" @blur="addKeywords">
                          <button class="btn sm" @click="expand" :disabled="expanding || !(spec.topic.description || spec.topic.keywords.length)"><Icon name="spark"/>{{ expanding ? '正在扩展…' : 'AI 扩展关键词' }}</button>
                          <button class="btn ghost sm" v-if="spec.topic.keywords.length" @click="spec.topic.keywords = []">清空</button>
                        </div>
                        <div class="muted small">{{ spec.topic.mode === 'text' ? '在聊天原文里逐字查找这些词，不区分大小写。' : '模型先读一遍范围内的全部消息，找出和话题有关的部分（包括没出现关键词的）；关键词命中的也算。比逐字查找贵得多。' }}</div>
                        <div class="row small">每处命中前后各带 <input type="number" class="input sm" style="width:64px" min="3" max="80" v-model.number="spec.topic.window"> 条，同一段对话里相邻的合在一起</div>
                      </template>
                    </div>
                  </div>
                </div>
              </div>

              <div class="col" style="gap:14px">
                <div class="card">
                  <div class="card-head"><h2>怎么写</h2></div>
                  <div class="card-body form">
                    <label class="k">视角</label>
                    <div class="col" style="gap:8px;align-items:stretch">
                      <div class="seg" style="align-self:flex-start"><button v-for="(v, k) in meta ? meta.perspectives : {}" :key="k" :class="{on: spec.perspective === k}" @click="spec.perspective = k">{{ v }}</button></div>
                      <div class="muted small">{{ PERSPECTIVE_HELP[spec.perspective] }}</div>
                      <div class="row wrap" v-if="spec.perspective === 'person'" style="gap:5px">
                        <span class="chip accent" v-if="spec.person">{{ spec.person.name }}<a href="" @click.prevent="spec.person = null" title="去掉">×</a></span>
                        <Picker v-else kind="person" placeholder="以谁的视角：备注名或昵称" @pick="c => spec.person = { id: c.id, name: c.name }"/></div>
                      <div class="row" v-if="spec.perspective !== 'mine'"><span class="small">称呼你为</span>
                        <input class="input sm" style="width:200px" v-model="spec.self_name" :placeholder="'留空时用你的微信昵称' + (meta ? '：' + meta.self_name : '')"></div>
                    </div>
                    <label class="k">模板</label>
                    <div class="col" style="gap:6px;align-items:stretch">
                      <div class="seg" style="align-self:flex-start"><button v-for="(v, k) in meta ? meta.templates : {}" :key="k" :class="{on: spec.template === k}" @click="spec.template = k">{{ v }}</button></div>
                      <div class="muted small">{{ TEMPLATE_HELP[spec.template] }}</div>
                    </div>
                    <label class="k">详略</label>
                    <div class="seg" style="justify-self:start"><button v-for="(v, k) in meta ? meta.details : {}" :key="k" :class="{on: spec.detail === k}" @click="spec.detail = k">{{ v }}</button></div>
                    <label class="k">侧重</label>
                    <textarea class="input" rows="3" v-model="spec.focus" placeholder="想多写的方面、语气和格式要求；留空按模板写"></textarea>
                    <label class="k">阅读用的模型</label>
                    <div class="col" style="gap:4px;align-items:stretch">
                      <div class="seg" style="align-self:flex-start"><button :class="{on: spec.reader === 'careful'}" @click="spec.reader = 'careful'">精读环节的模型</button>
                        <button :class="{on: spec.reader === 'fast'}" @click="spec.reader = 'fast'">扫读环节的模型（更省）</button></div>
                      <div class="muted small">成稿用"总览成稿"环节的模型，复核用"结论卡复核"环节的模型，筛查用"扫读"环节的模型。</div>
                    </div>
                  </div>
                </div>
                <div class="card">
                  <div class="card-head"><h2>下一步</h2></div>
                  <div class="card-body">
                    <div v-if="detail.blockers.length" class="col" style="gap:4px;align-items:flex-start">
                      <div class="muted">还要：</div>
                      <div v-for="b in detail.blockers" :key="b">· {{ b }}</div>
                    </div>
                    <div v-else class="col" style="gap:8px;align-items:flex-start">
                      <div>先<b>预览</b>：只在本机取数和查找，不调用模型，能看到会读哪些会话、命中了什么、大约要用多少 token。确认后再<b>生成</b>。</div>
                      <div class="muted small" v-if="spec.topic.mode === 'ai'">由模型判断相关时，命中要在<b>模型筛查</b>之后才知道；筛查会调用模型，生成时直接用筛查的结果。</div>
                      <div class="row"><button class="btn" @click="startJob('topic-plan')" :disabled="busy"><Icon name="search"/>预览</button>
                        <button class="btn" v-if="spec.topic.mode === 'ai'" @click="startJob('topic-screen')" :disabled="busy"><Icon name="spark"/>模型筛查</button>
                        <button class="btn primary" @click="startJob('topic-run')" :disabled="busy"><Icon name="play"/>生成</button></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- ===== 匹配预览 ===== -->
            <div v-show="tab === 'preview'">
              <div v-if="!plan" class="empty card">还没有预览。预览只在本机取数和查找，不调用模型。
                <div style="margin-top:10px"><button class="btn" @click="startJob('topic-plan')" :disabled="busy || detail.blockers.length > 0"><Icon name="search"/>预览</button></div></div>
              <template v-else>
                <div class="banner warn" v-if="planStale"><Icon name="warn"/><div class="grow">预览之后改过时间、范围、话题或阅读模型，下面是旧条件下的结果。</div>
                  <button class="btn sm" @click="startJob('topic-plan')" :disabled="busy">重新预览</button></div>
                <div class="banner info" v-if="plan.cut"><Icon name="info"/><div class="grow">所选时间还没有完全导出，数据截至 {{ plan.data_end }}。</div></div>
                <div class="tiles">
                  <div class="tile"><div class="k">要读的会话</div><div class="v">{{ fmtInt(view.chats) }}</div>
                    <div class="d">{{ excludedCount ? '去掉了 ' + excludedCount + ' 个' : plan.no_hits ? '另有 ' + plan.no_hits + (spec.topic.mode === 'none' ? ' 个会话没有可读的消息' : ' 个没有命中') : '所选时间内 ' + fmtInt(view.messages) + ' 条' }}</div></div>
                  <div class="tile" v-if="spec.topic.mode !== 'none'"><div class="k">命中</div><div class="v">{{ fmtInt(view.hits) }} 条</div><div class="d">{{ plan.screen ? '模型筛查 ' + plan.screen.answered + '/' + plan.screen.chunks + ' 块' : '按关键词逐字查找' }}</div></div>
                  <div class="tile"><div class="k">发给模型的消息</div><div class="v">{{ fmtInt(view.lines) }} 条</div><div class="d">{{ spec.topic.mode !== 'none' ? '含命中前后的上下文' : '所选时间内的全部消息，加前后 12 小时的上下文' }}</div></div>
                  <div class="tile"><div class="k">预计用量</div><div class="v">{{ fmtTok(view.tokens) }}</div><div class="d">{{ plan.estimate.basis }}，约 {{ view.minutes }} 分钟</div></div>
                </div>
                <div class="banner info" v-if="plan.screen && plan.screen.todo_est"><Icon name="info"/>
                  <div class="grow">还没有做模型筛查。筛查要读 {{ plan.screen.chunks }} 块，约 {{ fmtTok(plan.screen.todo_est * ratio) }} token；上面的命中目前只来自关键词。</div>
                  <button class="btn sm" @click="startJob('topic-screen')" :disabled="busy"><Icon name="spark"/>模型筛查</button></div>
                <div class="row wrap small muted" style="gap:14px;margin-bottom:12px">
                  <span>预览于 {{ plan.made_at.replace('T', ' ') }}</span>
                  <span v-for="s in plan.skipped" :key="s.reason">跳过（{{ s.reason }}）{{ s.chats }} 个会话</span>
                  <span v-if="plan.voice.total">语音 {{ plan.voice.total }} 条，已转写 {{ plan.voice.done }} 条</span>
                  <span v-if="maskText">遮蔽：{{ maskText }} <a href="" @click.prevent="masks = true">查看原文</a></span>
                </div>
                <div class="card">
                  <div class="card-head"><h2>会话</h2><span class="muted small">取消勾选的会话不读；点一行看命中的片段</span></div>
                  <div style="overflow:auto">
                    <table class="t">
                      <thead><tr><th style="width:34px"></th><th>会话</th><th>分类</th><th class="r">所选时间内</th><th class="r">我发</th>
                        <th class="r" v-if="spec.topic.mode !== 'none'">命中</th><th class="r">片段</th><th class="r">要读</th><th class="r">预计 token</th></tr></thead>
                      <tbody>
                        <template v-for="c in plan.chats" :key="c.chat">
                          <tr class="pick" :class="{off: excluded(c.chat), on: openChats[c.chat]}" @click="toggleChat(c)">
                            <td @click.stop><input type="checkbox" :checked="!excluded(c.chat)" @change="toggleExclude(c.chat)"></td>
                            <td>{{ c.name }}<span class="chip" v-if="c.via_people" style="margin-left:6px" title="只读所选的人在这个群里的发言和前后上下文">所选的人</span></td>
                            <td class="muted small">{{ catTitle(c.category) }}</td>
                            <td class="r num">{{ fmtInt(c.messages) }}</td><td class="r num">{{ fmtInt(c.self) }}</td>
                            <td class="r num" v-if="spec.topic.mode !== 'none'">{{ fmtInt(c.hits) }}</td>
                            <td class="r num">{{ c.windows || (c.hits ? '' : '全部') }}</td>
                            <td class="r num">{{ fmtInt(c.lines) }}</td><td class="r num">{{ fmtTok(c.est * ratio) }}</td>
                          </tr>
                          <tr v-if="openChats[c.chat]" class="wins-row"><td></td><td :colspan="spec.topic.mode !== 'none' ? 8 : 7">
                            <div v-if="!openChats[c.chat].windows.length && !openChats[c.chat].loading" class="muted small">{{ c.hits ? '没能读出片段，重新预览一次。' : '这个会话读全部内容，没有单独的命中片段。' }}</div>
                            <div class="win" v-for="(w, wi) in openChats[c.chat].windows" :key="wi">
                              <div v-for="l in w" :key="l.lid + l.time" class="wl" :class="{hit: l.hit, me: l.self}">
                                <span class="muted">{{ l.time.slice(5) }}</span> <template v-if="l.sender"><b>{{ l.sender }}</b>：</template><template v-for="(seg, si) in marked(l.body)" :key="si"><mark v-if="seg.k" class="kw">{{ seg.t }}</mark><template v-else>{{ seg.t }}</template></template>
                                <span class="why" v-if="l.why">模型：{{ l.why }}</span>
                                <a href="" v-if="l.lid" class="small" @click.prevent="lid = l.lid">上下文</a>
                              </div>
                            </div>
                            <div class="row" style="margin-top:6px" v-if="openChats[c.chat].loading || openChats[c.chat].windows.length < openChats[c.chat].total">
                              <span v-if="openChats[c.chat].loading" class="spinner"></span>
                              <button v-else class="btn sm" @click="moreWindows(c.chat)">再显示 {{ Math.min(20, openChats[c.chat].total - openChats[c.chat].windows.length) }} 段（共 {{ openChats[c.chat].total }} 段）</button></div>
                          </td></tr>
                        </template>
                      </tbody>
                    </table>
                  </div>
                </div>
              </template>
            </div>

            <!-- ===== 结果 ===== -->
            <div v-show="tab === 'result'">
              <div v-if="!report" class="empty card">还没有生成。{{ detail.blockers.length ? '先在"配置"里补全：' + detail.blockers.join('、') + '。' : '' }}</div>
              <template v-else>
                <div class="banner warn" v-if="reportStale"><Icon name="warn"/><div class="grow">这份结果生成之后，改过时间、范围、话题或阅读模型。</div></div>
                <div class="banner warn" v-else-if="repairable"><Icon name="warn"/>
                  <div class="grow">有 {{ Object.keys(detail.status.failures).length }} 处调用失败，结果里缺了这部分内容。补做只重新调用失败的部分和用到它的成稿，其余直接用已有结果。</div>
                  <button class="btn sm primary" @click="startJob('topic-run')" :disabled="busy"><Icon name="play"/>补做失败的部分</button></div>
                <div class="row wrap" style="margin-bottom:10px;gap:6px">
                  <span class="muted small">生成于 {{ (report.made || '').replace('T', ' ') }}</span><span class="spacer"></span>
                  <button class="btn sm" @click="copy(report.markdown, 'Markdown')"><Icon name="copy"/>复制 Markdown</button>
                  <button class="btn sm" @click="copy(report.text, '纯文本')" :disabled="!report.text" title="去掉格式和本机链接，可以直接粘到微信"><Icon name="copy"/>复制纯文本</button>
                  <button class="btn sm" @click="openPath(report.path)"><Icon name="ext"/>打开文件</button>
                  <button class="btn sm" @click="openPath(report.path, true)"><Icon name="folder"/>在资源管理器中显示</button>
                  <button class="btn sm" v-if="detail.masks" @click="masks = true"><Icon name="search"/>遮蔽原文</button>
                </div>
                <div class="card"><div class="md" v-html="html" @click="onReportClick"></div></div>
              </template>
            </div>
          </div>
        </div>
      </template>
    </div>
    <div class="rside" v-if="lid"><ContextView :lid="lid" @close="lid = null"/></div>
  </div>
  <MaskModal v-if="masks" :url="'/api/topics/' + id + '/masks'" @close="masks = false"/>
  <Modal v-if="log" title="日志" wide @close="log = null"><pre class="log">{{ log.text || '（空）' }}</pre></Modal>
</div>`,
  setup() {
    const UNITS = [['day', '一天'], ['month', '一个月'], ['year', '一年'], ['custom', '自定义']];
    const PERSPECTIVE_HELP = { mine: '写给你自己：这些事对你意味着什么、你要做什么。', neutral: '不站在任何一方，只写发生了什么、谁说了什么，适合给别人看。',
      person: '写给指定的那个人：称他为"你"，写对他意味着什么。只依据聊天内容，不揣测想法。' };
    const TEMPLATE_HELP = { brief: '几句话的结论加要点，一屏读完。', minutes: '按事情或时间分节，写清经过、各方说法、结论和未定的事。',
      timeline: '按日期排列发生了什么，最后写目前的状态。', research: '围绕话题整理各方观点、事实和数据、分歧和资源。', decisions: '已经决定的事，和还要做的事（谁、做什么、期限）。' };
    const list = ref([]), meta = ref(null), detail = ref(null), spec = ref(null), tab = ref('config');
    const saving = ref(''), drafting = ref(false), expanding = ref(false), problems = ref([]), kwText = ref('');
    const openChats = reactive({}), lid = ref(null), masks = ref(false), log = ref(null), ask = ref(null), scroller = ref(null);
    const id = computed(() => route.arg || '');
    const plan = computed(() => detail.value?.plan || null);
    const report = computed(() => detail.value?.report || null);
    const search = ref('');
    const planStale = computed(() => !!plan.value && plan.value.search !== search.value);
    const reportStale = computed(() => !!report.value && report.value.search !== search.value);
    // Finished with failed calls under the same settings: running again asks only for those, the rest is cached.
    const repairable = computed(() => !!report.value && !reportStale.value && detail.value?.status?.state === 'done-with-problems'
      && Object.keys(detail.value.status.failures || {}).length > 0);
    const job = computed(() => { const c = store.jobs.current; return c && c.month === '专题/' + id.value ? c : null; });
    const queued = computed(() => store.jobs.queue.some(j => j.month === '专题/' + id.value));
    const busy = computed(() => !!job.value || queued.value);
    const html = computed(() => report.value ? md.render(report.value.markdown, {}) : '');
    const excluded = chat => spec.value.exclude_chats.includes(chat);
    const excludedCount = computed(() => (plan.value?.chats || []).filter(c => excluded(c.chat)).length);
    // The plan's estimate scaled from its own read and screen sizes, so dropping chats changes it right away.
    const ratio = computed(() => { const p = plan.value; return p ? p.estimate.tokens / Math.max(1, p.read_est + (p.screen ? p.screen.todo_est : 0)) : 1; });
    const view = computed(() => {
      const p = plan.value, kept = (p?.chats || []).filter(c => !excluded(c.chat));
      const raw = kept.reduce((s, c) => s + c.est, 0) + (p?.screen ? p.screen.todo_est : 0);
      const all = p ? p.read_est + (p.screen ? p.screen.todo_est : 0) : 0;
      return { chats: kept.length, messages: kept.reduce((s, c) => s + c.messages, 0), hits: kept.reduce((s, c) => s + c.hits, 0),
               lines: kept.reduce((s, c) => s + c.lines, 0), tokens: raw * ratio.value, minutes: p ? Math.max(1, Math.round(p.estimate.minutes * raw / Math.max(1, all))) : 0 };
    });
    const maskText = computed(() => Object.entries(plan.value?.masks || {}).map(([k, v]) => `${k} ${v}`).join('、'));
    const lastFail = computed(() => {
      const d = detail.value;
      if (!d) return null;
      const rec = store.jobs.history.find(r => r.month === '专题/' + id.value);
      if (!rec || !['failed', 'budget-stop'].includes(rec.state)) return null;
      return { what: TOPIC_JOB[rec.kind] || '', message: rec.message || '请查看日志。', log: rec.log };
    });

    // Tasks waiting for an external agent: from the running job while there is one, else from the 专题 as last loaded.
    const handoff = computed(() => job.value ? job.value.handoff || null : detail.value?.handoff || null);
    async function finish(apiOnly) {
      const r = await addJobs([{ kind: 'topic-run', topic: id.value, ...(apiOnly ? { api_only: true } : {}) }]);
      if (r && r.added.length) loadList();
    }

    async function loadList() { try { list.value = (await api('/api/topics')).topics; } catch (e) { fail(e); } }
    let skipSave = false;
    async function loadDetail() {
      if (!id.value) { detail.value = null; spec.value = null; return; }
      try {
        const d = await api('/api/topics/' + encodeURIComponent(id.value));
        detail.value = d; search.value = d.search;
        if (!spec.value || spec.value._id !== id.value || !saveTimer) { skipSave = true; spec.value = { ...d.spec, _id: id.value }; }
      } catch (e) { fail(e); detail.value = null; }
    }
    let saveTimer = null;
    watch(spec, () => {
      if (skipSave) { skipSave = false; return; }
      if (!spec.value) return;
      saving.value = '未保存';
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, 600);
    }, { deep: true });
    async function save() {
      clearTimeout(saveTimer); saveTimer = null;
      if (!spec.value || !detail.value) return;
      const { _id, ...body } = spec.value;
      saving.value = '正在保存…';
      try {
        const r = await api('/api/topics/' + encodeURIComponent(_id), { method: 'PUT', body: { spec: body } });
        if (_id !== id.value) return;
        Object.assign(detail.value, { blockers: r.blockers, missing: r.missing, scope: r.scope });
        search.value = r.search; saving.value = '已保存';
        const it = list.value.find(t => t.id === _id);
        if (it) { it.title = r.spec.title; it.range = r.spec.range; }
      } catch (e) { saving.value = '没有保存：' + e.message; }
    }
    async function flush() { if (saveTimer) await save(); }

    async function create() {
      const last = meta.value?.last_day;
      let range = { unit: 'month', from: '', to: '' };
      if (last) {
        const ym = last === monthEnd(last.slice(0, 7)) ? last.slice(0, 7) : shiftMonth(last.slice(0, 7), -1);
        range = { unit: 'month', from: ym + '-01', to: monthEnd(ym) };
      }
      try {
        const d = await api('/api/topics', { method: 'POST', body: { spec: { range } } });
        await loadList();
        tab.value = 'config'; problems.value = [];
        go('topics', d.id);
        await nextTick(); setTimeout(() => ask.value && ask.value.focus(), 200);
      } catch (e) { fail(e); }
    }
    async function duplicate() {
      await flush();
      const { _id, ...body } = spec.value;
      try {
        const d = await api('/api/topics', { method: 'POST', body: { spec: { ...body, title: (body.title || '专题') + '（副本）' } } });
        await loadList(); go('topics', d.id); toast('已另存为新专题', 'ok');
      } catch (e) { fail(e); }
    }
    async function remove() {
      if (!await confirmBox('删除专题', `"${spec.value.title || '未命名专题'}"的配置、预览和结果、缓存和用量记录会移到回收站，可以从回收站恢复。用量页不再显示这个专题。`, { ok: '删除', danger: true })) return;
      try {
        clearTimeout(saveTimer); saveTimer = null;
        await api('/api/topics/' + encodeURIComponent(id.value), { method: 'DELETE' });
        toast('专题已删除，文件在回收站', 'ok');
        await loadList(); go('topics', list.value[0]?.id || '');
      } catch (e) { fail(e); }
    }

    async function draft() {
      if (!spec.value.request.trim() || drafting.value) return;
      await flush();
      drafting.value = true;
      try {
        const r = await api(`/api/topics/${id.value}/draft`, { method: 'POST', body: { request: spec.value.request } });
        skipSave = true; spec.value = { ...r.spec, _id: id.value };
        Object.assign(detail.value, { blockers: r.blockers, scope: r.scope }); problems.value = r.problems;
        await loadDetail(); loadList();
        toast(r.problems.length ? '已拆解，有几个名字需要你确认' : '已拆解成配置，可以继续修改', r.problems.length ? 'warn' : 'ok');
      } catch (e) { fail(e); }
      drafting.value = false;
    }
    async function settle(p, c) {
      problems.value.splice(problems.value.indexOf(p), 1);
      if (p.kind === 'person') { spec.value.person = { id: c.id, name: c.name }; return; }
      // A chat or person the draft could not settle goes into this 专题's own selection.
      await flush();
      try {
        const r = await api(`/api/topics/${encodeURIComponent(id.value)}/scope`, { method: 'POST', body: { pick: { id: c.id, name: c.name } } });
        skipSave = true; spec.value = { ...spec.value, scope: r.spec.scope };
        Object.assign(detail.value, { blockers: r.blockers, scope: r.scope }); search.value = r.search;
      } catch (e) { fail(e); }
    }
    async function openScope() { await flush(); go('chats', id.value); }
    function addKeywords() {
      for (const k of kwText.value.split(/[、，,;；\n]+/).map(s => s.trim()).filter(Boolean))
        if (!spec.value.topic.keywords.includes(k)) spec.value.topic.keywords.push(k);
      kwText.value = '';
    }
    async function expand() {
      addKeywords();
      expanding.value = true;
      try {
        const r = await api(`/api/topics/${id.value}/keywords`, { method: 'POST', body: { description: spec.value.topic.description, keywords: spec.value.topic.keywords } });
        spec.value.topic.keywords.push(...r.keywords.filter(k => !spec.value.topic.keywords.includes(k)));
        toast(r.keywords.length ? `补充了 ${r.keywords.length} 个关键词，用不上的可以点 × 去掉` : '没有可以补充的关键词', r.keywords.length ? 'ok' : 'info');
      } catch (e) { fail(e); }
      expanding.value = false;
    }

    // ----- time -----
    function setUnit(u) {
      const r = spec.value.range, base = r.from || meta.value?.last_day || '';
      if (u === 'day') Object.assign(r, { unit: u, from: base, to: base });
      else if (u === 'month' && base) Object.assign(r, { unit: u, from: base.slice(0, 7) + '-01', to: monthEnd(base.slice(0, 7)) });
      else if (u === 'year' && base) Object.assign(r, { unit: u, from: base.slice(0, 4) + '-01-01', to: base.slice(0, 4) + '-12-31' });
      else r.unit = u;
    }
    function setDay(v) { if (v) Object.assign(spec.value.range, { from: v, to: v }); }
    function setMonth(v) { if (v) Object.assign(spec.value.range, { from: v + '-01', to: monthEnd(v) }); }
    function setYear(v) { if (/^\d{4}$/.test(v)) Object.assign(spec.value.range, { from: v + '-01-01', to: v + '-12-31' }); }

    // ----- jobs -----
    async function startJob(kind) {
      await flush();
      if (detail.value.blockers.length) { tab.value = 'config'; return toast('还要' + detail.value.blockers.join('、'), 'warn'); }
      if (kind !== 'topic-plan' && detail.value.missing.length) return toast('这些环节还没有选择模型：' + detail.value.missing.join('、'), 'warn');
      const p = plan.value;
      if (kind === 'topic-screen') {
        const est = p && p.screen && !planStale.value ? `约 ${fmtTok(p.screen.todo_est * ratio.value)} token` : '用量要先预览才知道';
        if (!await confirmBox('模型筛查', `模型会读一遍范围内的全部消息，找出和话题有关的部分，${est}。筛查完先给你看结果，不会接着总结。`, { ok: '开始筛查' })) return;
      }
      if (kind === 'topic-run' && !repairable.value) {
        let body;
        if (!p || planStale.value) body = '还没有按现在的配置预览过，不知道要用多少 token。可以先预览，看看会读哪些内容。';
        else body = `预计约 ${fmtTok(view.value.tokens)} token、${view.value.minutes} 分钟（${p.estimate.basis}），读 ${view.value.chats} 个会话里的 ${fmtInt(view.value.lines)} 条消息。`
          + (p.screen && p.screen.todo_est ? '\n其中包括先做一遍模型筛查。' : '');
        if (report.value) body += '\n\n会替换现有的结果；以前调用过、条件也没变的部分直接用缓存。';
        if (!await confirmBox(report.value ? '重新生成专题' : '生成专题', body, { ok: '生成' })) return;
      }
      const r = await addJobs([{ kind, topic: id.value }]);
      if (r && r.added.length) loadList();
    }
    async function stop() {
      if (!await confirmBox('停止', '停止以后，已经完成的调用都保存在缓存中，再次运行时不会重做。确定停止吗？', { ok: '停止', danger: true })) return;
      try { store.jobs = await api('/api/jobs/stop', { method: 'POST', body: {} }); } catch (e) { fail(e); }
    }
    async function showLog(path) {
      if (!path) return toast('没有日志', 'warn');
      try { log.value = { path, text: (await api('/api/jobs/log?path=' + encodeURIComponent(path))).text }; } catch (e) { fail(e); }
    }
    // When this 专题's job ends, show what it made.
    watch(() => store.jobs.current?.id, (now, before) => {
      const rec = before && store.jobs.history.find(r => r.id === before);
      if (!rec || rec.month !== '专题/' + id.value) return;
      for (const k of Object.keys(openChats)) delete openChats[k];
      loadDetail(); loadList();
      if (['done', 'done-with-problems'].includes(rec.state)) tab.value = rec.kind === 'topic-run' ? 'result' : 'preview';
    });

    // ----- preview -----
    function toggleExclude(chat) {
      const a = spec.value.exclude_chats, i = a.indexOf(chat);
      i >= 0 ? a.splice(i, 1) : a.push(chat);
    }
    async function toggleChat(c) {
      if (openChats[c.chat]) { delete openChats[c.chat]; return; }
      openChats[c.chat] = { loading: false, windows: [], total: 0 };
      if (c.hits || c.via_people) await moreWindows(c.chat);
    }
    async function moreWindows(chat) {
      const o = openChats[chat];
      o.loading = true;
      try {
        const r = await api(`/api/topics/${id.value}/matches?chat=${encodeURIComponent(chat)}&offset=${o.windows.length}&limit=20`);
        // The model gives one reason per run of related lines; show it once where it starts.
        for (const w of r.windows) { let last = ''; for (const l of w) { if (l.why === last) l.why = ''; else last = l.why; } }
        o.windows.push(...r.windows); o.total = r.total;
      } catch (e) { fail(e); }
      o.loading = false;
    }
    const kwPattern = computed(() => {
      const ks = (spec.value?.topic.keywords || []).filter(Boolean).sort((a, b) => b.length - a.length);
      return ks.length && spec.value.topic.mode !== 'none' ? new RegExp(ks.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'gi') : null;
    });
    function marked(text) {
      const re = kwPattern.value;
      if (!re) return [{ t: text, k: false }];
      const out = []; let last = 0; re.lastIndex = 0;
      for (const m of text.matchAll(re)) { if (m.index > last) out.push({ t: text.slice(last, m.index), k: false }); out.push({ t: m[0], k: true }); last = m.index + m[0].length; }
      if (last < text.length) out.push({ t: text.slice(last), k: false });
      return out;
    }

    // ----- result -----
    function onReportClick(ev) {
      const a = ev.target.closest('a');
      if (!a) return;
      ev.preventDefault();
      if (a.classList.contains('ref')) { lid.value = +a.dataset.lid; return; }
      const href = a.dataset.href || '';
      if (/^https?:\/\//i.test(href)) api('/api/open_url', { method: 'POST', body: { url: href } }).catch(fail);
    }
    async function copy(text, what) {
      try { await navigator.clipboard.writeText(text); toast(`已复制${what}`, 'ok'); } catch (e) { fail(e); }
    }

    watch(id, async () => { problems.value = []; lid.value = null; for (const k of Object.keys(openChats)) delete openChats[k]; await flush(); detail.value = null; spec.value = null; await loadDetail(); });
    onMounted(async () => {
      try { meta.value = await api('/api/topics/meta'); } catch (e) { fail(e); }
      await loadList();
      // The list can take a while; by then another page may be showing, and it stays.
      if (route.page === 'topics' && !id.value && list.value[0]) history.replaceState(null, '', '#/topics/' + list.value[0].id), parseRoute();
      await loadDetail();
      if (detail.value && !detail.value.spec.request && !detail.value.report) setTimeout(() => ask.value && ask.value.focus(), 100);
      else if (detail.value?.report) tab.value = 'result';
    });
    onBeforeUnmount(() => { if (saveTimer) save(); });
    return { UNITS, PERSPECTIVE_HELP, TEMPLATE_HELP, TOPIC_JOB, list, meta, detail, spec, tab, saving, drafting, expanding, problems, kwText,
             openChats, lid, masks, log, ask, scroller, id, plan, report, planStale, reportStale, repairable, job, queued, busy, html, view, ratio, maskText,
             excludedCount, lastFail, handoff, finish, excluded, catTitle, create, duplicate, remove, draft, settle, openScope, addKeywords, expand,
             setUnit, setDay, setMonth, setYear, startJob, stop, showLog, toggleExclude, toggleChat, moreWindows, marked, onReportClick, copy,
             go, openPath, rangeText, monthLabel, stateKind, fmtTok, fmtInt };
  },
};
