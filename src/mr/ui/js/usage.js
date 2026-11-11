/* 用量: what each report and 专题 cost, where it went (stage × model), and how 月报 compare with the budget. */
const PALETTE = ['#4e79d8', '#e08b3a', '#3fa877', '#d0564b', '#8a6fd1', '#2fa3b5', '#c4a334', '#c46aa8', '#7f8c9d', '#5b9e45'];

MRPages.usage = {
  components: { Icon, Modal },
  template: `
<div class="page">
  <div class="page-head">
    <h1>用量</h1>
    <div class="seg"><button :class="{on: kind === 'month'}" @click="setKind('month')">月报</button><button :class="{on: kind === 'topic'}" @click="setKind('topic')">专题</button></div>
    <span class="sub">现有的每期月报和每个专题实际用了多少。重新生成后只算新的这一版，删除的不再显示。</span>
    <span class="spacer"></span>
    <button class="btn" @click="exportCsv" :disabled="!rows.length" :title="'导出所选' + kindName + '的每一次调用'"><Icon name="download"/>导出 CSV</button>
    <button class="btn" @click="restart" title="这一页只统计从现在开始的调用；之前的可以按月报、专题挑着恢复"><Icon name="refresh"/>重新开始统计</button>
    <button class="btn" @click="load"><Icon name="refresh"/>刷新</button>
  </div>
  <div class="banner info" v-if="data && data.since && data.hidden.length"><Icon name="info"/>
    <div class="grow">从 {{ fmtTime(data.since) }} 开始统计。{{ data.hidden.length }} 个月报或专题在这之前的用量没有计入这一页；月度预算照常计算。</div>
    <button class="btn sm" @click="openRestore">选择要恢复的…</button></div>
  <div v-if="!data" class="empty"><span class="spinner"></span></div>
  <div v-else-if="!kindRows.length" class="empty card">{{ kind === 'month'
    ? (data.since ? '这段时间没有月报的调用记录。' : '还没有月报的调用记录。生成一份月报以后，这里会显示它用了多少。')
    : (data.since ? '这段时间没有专题的调用记录。' : '还没有专题的调用记录。生成一个专题以后，这里会显示它用了多少。') }}</div>
  <template v-else>
    <div class="card" style="margin-bottom:14px">
      <div class="card-head"><h2>{{ kind === 'month' ? '各月月报' : '各个专题' }}</h2><span class="muted small">点一行看它的用量去向</span><span class="spacer"></span>
        <template v-if="kind === 'month'">
          <span class="small" v-if="suggest">最近 3 期平均 {{ fmtTok(avg) }} · 建议每月预算 {{ fmtTok(suggest) }}</span>
          <button class="btn sm" v-if="suggest && store.overview && store.overview.budget !== suggest" @click="setBudget">设为每月预算</button>
        </template>
        <span class="muted small" v-else>专题不计入每月预算</span></div>
      <div class="usage-months">
        <div class="chart sm" ref="monthly"></div>
        <div style="overflow:auto">
          <table class="t small">
            <thead><tr><th>{{ kindName }}</th><th class="r">token</th><th class="r">费用</th><th class="r">调用</th><th class="r">失败</th></tr></thead>
            <tbody>
              <tr class="pick" :class="{ on: !pick }" @click="pick = ''"><td>全部{{ kindName }}</td><td class="r num">{{ fmtTok(total.in + total.out) }}</td>
                <td class="r num">{{ money(total) }}</td><td class="r num">{{ fmtInt(total.calls) }}</td><td class="r num">{{ total.calls - total.ok || '' }}</td></tr>
              <tr v-for="g in groups" :key="g.owner" class="pick" :class="{ on: pick === g.owner }" @click="pick = g.owner">
                <td>{{ name(g.owner) }}<span class="chip accent" v-if="running(g.owner)" style="margin-left:6px">正在生成</span>
                  <span class="chip" :class="stateKind(data.states[g.owner])" v-else-if="unfinished(g.owner)" style="margin-left:6px" title="还没有生成完，这里是到目前为止用掉的">{{ stateText(data.states[g.owner]) }}</span>
                  <span class="chip warn" v-if="kind === 'month' && budget && g.in + g.out > budget" style="margin-left:6px">超出预算</span></td>
                <td class="r num">{{ fmtTok(g.in + g.out) }}</td><td class="r num">{{ money(g) }}</td>
                <td class="r num">{{ fmtInt(g.calls) }}</td><td class="r num">{{ g.calls - g.ok || '' }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>{{ !pick ? '全部' + kindName : title(pick) }} · 用在了哪里</h2><span class="spacer"></span>
        <span class="muted small" v-if="!sum.priced"><a href="#/settings/models">填写模型单价</a>以后显示费用</span></div>
      <div class="card-body">
        <div class="tiles" style="margin-bottom:14px">
          <div class="tile"><div class="k">token</div><div class="v">{{ fmtTok(sum.in + sum.out) }}</div><div class="d">输入 {{ fmtTok(sum.in) }} · 输出 {{ fmtTok(sum.out) }}</div></div>
          <div class="tile"><div class="k">费用（估算）</div><div class="v">{{ money(sum) }}</div>
            <div class="d">{{ sum.unpriced ? sum.unpriced + ' 次调用的模型没有单价' : sum.local && sum.local === sum.ok ? '本地模型或外部 Agent，不计费' : '按设置中的单价计算' + (sum.local ? '；本地模型和外部 Agent 不计费' : '') }}</div></div>
          <div class="tile"><div class="k">调用</div><div class="v">{{ fmtInt(sum.calls) }}</div>
            <div class="d">{{ sum.calls - sum.ok ? '失败 ' + (sum.calls - sum.ok) + ' 次，失败的调用不产生用量' : '全部成功' }}</div></div>
          <div class="tile" v-if="sum.cached"><div class="k">服务商缓存</div><div class="v">{{ pct(sum.cached, sum.in) }}</div><div class="d">输入中按缓存价计费的部分</div></div>
        </div>
        <div class="banner info" v-if="insight"><Icon name="info"/><div class="grow">{{ insight }}</div></div>
        <div class="chart sm" ref="matrix" style="margin-top:10px"></div>
      </div>
      <div style="overflow:auto">
        <table class="t">
          <thead><tr><th>环节</th><th>模型</th><th class="r">占比</th><th class="r">token</th><th class="r">输入</th><th class="r">输出</th><th class="r">调用</th><th class="r">失败</th><th class="r">费用</th></tr></thead>
          <tbody><tr v-for="r in table" :key="r.stage + r.model">
            <td>{{ label(r.stage) }}</td><td>{{ r.model }}</td><td class="r num">{{ pct(r.in + r.out, sum.in + sum.out) }}</td>
            <td class="r num">{{ fmtTok(r.in + r.out) }}</td><td class="r num">{{ fmtTok(r.in) }}</td><td class="r num">{{ fmtTok(r.out) }}</td>
            <td class="r num">{{ fmtInt(r.calls) }}</td><td class="r num">{{ r.calls - r.ok || '' }}</td><td class="r num">{{ money(r) }}</td>
          </tr></tbody>
        </table>
      </div>
    </div>
  </template>
  <Modal v-if="restore" title="恢复之前的统计" @close="restore = null">
    <div class="muted small" style="margin-bottom:10px">勾选要算回来的月报或专题，它们在 {{ fmtTime(data.since) }} 之前的用量会重新计入这一页。没有勾选的保持不计。</div>
    <div style="max-height:50vh;overflow:auto">
      <table class="t small">
        <thead><tr><th style="width:34px"><input type="checkbox" :checked="restore.length === data.hidden.length" @change="restore = $event.target.checked ? data.hidden.map(h => h.owner) : []" title="全选"></th>
          <th>月报或专题</th><th class="r">没有计入的 token</th><th class="r">调用</th></tr></thead>
        <tbody><tr v-for="h in data.hidden" :key="h.owner" class="pick" @click="toggleRestore(h.owner)">
          <td><input type="checkbox" :checked="restore.includes(h.owner)" @click.stop="toggleRestore(h.owner)"></td>
          <td>{{ title(h.owner) }}</td><td class="r num">{{ fmtTok(h.tokens) }}</td><td class="r num">{{ fmtInt(h.calls) }}</td></tr></tbody>
      </table>
    </div>
    <template #foot><button class="btn" @click="restore = null">取消</button>
      <button class="btn primary" :disabled="!restore.length" @click="doRestore">恢复所选（{{ restore.length }}）</button></template>
  </Modal>
</div>`,
  setup() {
    const data = ref(null);
    const restore = ref(null);        // the owners ticked in the restore dialog; null while it is closed
    const kind = ref(loadLocal('usage.kind', null));   // 'month' or 'topic'; null until the data picks one
    const pick = ref(null);           // a month or '专题/<id>'; '' = all of this kind; null until the data picks the latest
    const monthly = ref(null), matrix = ref(null);
    const charts = {};
    const C = computed(() => Object.fromEntries((data.value?.columns || []).map((c, i) => [c, i])));
    const label = s => data.value?.stage_labels[s] || s;
    const isTopic = o => o.startsWith('专题/');
    const kindName = computed(() => kind.value === 'topic' ? '专题' : '月报');
    const kindOf = o => isTopic(o) ? 'topic' : 'month';
    // A 专题 by its title.
    const name = o => isTopic(o) ? data.value?.topics[o.slice(3)] || '未命名专题' : monthLabel(o);
    // With what it is, for headings.
    const title = o => isTopic(o) ? '专题「' + name(o) + '」' : monthLabel(o) + ' 月报';
    // A result that is still being made is listed with what it has used so far, and says so.
    const running = o => store.jobs.current?.month === o && ['run', 'topic-run', 'topic-screen'].includes(store.jobs.current.kind);
    const unfinished = o => { const s = data.value?.states[o]; return !!s && !['done', 'done-with-problems'].includes(s); };
    const all = computed(() => data.value?.rows || []);
    const kindRows = computed(() => all.value.filter(r => kindOf(r[C.value.month]) === kind.value));
    const rows = computed(() => kindRows.value.filter(r => !pick.value || r[C.value.month] === pick.value));
    const avg = computed(() => store.overview?.average_tokens);
    const suggest = computed(() => store.overview?.suggested_budget);
    const budget = computed(() => store.overview?.budget);

    function add(acc, r) {
      const c = C.value;
      acc.calls++;
      if (!r[c.ok]) return acc;
      acc.ok++; acc.in += r[c.in]; acc.out += r[c.out]; acc.cached += r[c.cached];
      if (r[c.cost] === null) acc.unpriced++;
      else if (r[c.currency] === 'local') { acc.priced = true; acc.local++; }
      else { acc.priced = true; const cur = r[c.currency] || 'USD'; acc.cost[cur] = (acc.cost[cur] || 0) + r[c.cost]; }
      return acc;
    }
    const blank = extra => ({ calls: 0, ok: 0, in: 0, out: 0, cached: 0, unpriced: 0, local: 0, priced: false, cost: {}, ...extra });
    const money = s => { const e = Object.entries(s.cost || {}); return e.length ? e.map(([cur, v]) => fmtMoney(v, cur)).join(' + ') : s.local ? '不计费' : '—'; };
    const total = computed(() => kindRows.value.reduce(add, blank()));
    const sum = computed(() => rows.value.reduce(add, blank()));
    // Newest first: months by name, 专题 by their folder name, which is when they were made.
    const groups = computed(() => {
      const g = {};
      for (const r of kindRows.value) { const o = r[C.value.month]; add(g[o] = g[o] || blank({ owner: o }), r); }
      return Object.values(g).sort((a, b) => b.owner.localeCompare(a.owner));
    });
    const table = computed(() => {
      const c = C.value, g = {};
      for (const r of rows.value) { const k = r[c.stage] + '|' + r[c.model_id]; add(g[k] = g[k] || blank({ stage: r[c.stage], model: r[c.model_id] }), r); }
      return Object.values(g).sort((a, b) => (b.in + b.out) - (a.in + a.out));
    });
    // The one thing worth acting on: which stage takes most, and on which model.
    const insight = computed(() => {
      const t = sum.value.in + sum.value.out, top = table.value[0];
      if (!t || !top) return '';
      const share = (top.in + top.out) / t;
      const inShare = sum.value.in / t;
      let s = `用量最多的是"${label(top.stage)}"（${top.model}），占 ${Math.round(share * 100)}%。`;
      if (share >= 0.4) s += '想省用量，先看这一环节：换更便宜的模型，或在设置中调整它读多少内容。';
      if (inShare >= 0.9) s += `输入占 ${Math.round(inShare * 100)}%，用量主要来自读入的聊天记录，而不是模型写出的内容。`;
      return s;
    });

    // ----- charts -----
    const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    function base() {
      return { color: PALETTE, textStyle: { fontFamily: 'Segoe UI, Microsoft YaHei UI, sans-serif', color: css('--text-2') },
               grid: { left: 56, right: 18, top: 34, bottom: 30 }, legend: { top: 0, type: 'scroll', textStyle: { color: css('--text-2') } },
               tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: css('--panel'), borderColor: css('--line'), textStyle: { color: css('--text') }, valueFormatter: fmtTok } };
    }
    function chart(name, el) {
      if (!el) return null;
      if (charts[name] && charts[name].getDom() !== el) { charts[name].dispose(); delete charts[name]; }
      return charts[name] = charts[name] || echarts.init(el, null, { renderer: 'canvas' });
    }
    function draw() {
      if (!data.value || !kindRows.value.length) return;
      const c = C.value;
      const ax = { axisLine: { lineStyle: { color: css('--line-2') } }, axisLabel: { color: css('--text-3') }, splitLine: { lineStyle: { color: css('--line') } } };
      // Each month or 专题, stacked by stage; months also against the budget.
      const mo = chart('monthly', monthly.value);
      if (mo) {
        const xs = groups.value.map(g => g.owner).reverse();
        const st = [...new Set(kindRows.value.map(r => r[c.stage]))];
        const cell = {};
        kindRows.value.forEach(r => { if (r[c.ok]) { const k = r[c.month] + '|' + r[c.stage]; cell[k] = (cell[k] || 0) + r[c.in] + r[c.out]; } });
        const series = st.map(s => ({ name: label(s), type: 'bar', stack: 't', barMaxWidth: 40, data: xs.map(x => cell[x + '|' + s] || 0) }));
        const lines = [], line = kind.value === 'month' ? budget.value || suggest.value : 0;
        if (kind.value === 'month' && budget.value) lines.push({ yAxis: budget.value, lineStyle: { color: '#d0564b' }, label: { position: 'insideEndTop', formatter: '每月预算 ' + fmtTok(budget.value), color: '#d0564b' } });
        else if (kind.value === 'month' && suggest.value) lines.push({ yAxis: suggest.value, lineStyle: { color: '#e08b3a', type: 'dashed' }, label: { position: 'insideEndTop', formatter: '建议预算 ' + fmtTok(suggest.value), color: '#e08b3a' } });
        if (lines.length && series.length) series[series.length - 1].markLine = { symbol: 'none', data: lines };
        // Labels never run into each other: months are written short, and when there are more bars than labels fit,
        // every second, third… one is labelled, counted from the newest so that one always shows.
        const month = kind.value === 'month', each = month ? 58 : 104;
        const step = Math.max(1, Math.ceil(xs.length * each / Math.max(1, mo.getWidth() - 74)));
        const shown = i => (xs.length - 1 - i) % step === 0;
        mo.setOption({ ...base(), xAxis: { type: 'category', data: xs.map(x => month ? x : name(x)), ...ax,
            axisLabel: { color: css('--text-3'), interval: shown, width: each - 8, overflow: 'truncate' }, axisTick: { alignWithLabel: true, interval: shown } },
          yAxis: { type: 'value', ...ax, axisLabel: { color: css('--text-3'), formatter: fmtTok }, max: v => Math.max(v.max, line || 0) * 1.1 }, series }, true);
        mo.off('click');
        mo.on('click', p => { pick.value = xs[p.dataIndex]; });
      }
      // Where the selected report's tokens went: stage × model.
      const m = chart('matrix', matrix.value);
      if (m) {
        const st = [...new Set(table.value.map(r => r.stage))], md = [...new Set(table.value.map(r => r.model))].sort();
        const cell = {};
        table.value.forEach(r => { cell[r.stage + '|' + r.model] = r.in + r.out; });
        m.setOption({ ...base(), grid: { left: 90, right: 18, top: 34, bottom: 24 },
          yAxis: { type: 'category', data: st.map(label), inverse: true, ...ax }, xAxis: { type: 'value', ...ax, axisLabel: { color: css('--text-3'), formatter: fmtTok } },
          series: md.map(x => ({ name: x, type: 'bar', stack: 't', barMaxWidth: 18, data: st.map(s => cell[s + '|' + x] || 0) })) }, true);
      }
    }
    function setKind(k) {
      if (kind.value === k) return;
      kind.value = k; saveLocal('usage.kind', k);
      pick.value = groups.value[0]?.owner || '';
    }
    async function load() {
      try { data.value = await api('/api/usage'); if (!store.overview) await loadOverview(); } catch (e) { fail(e); }
      // First visit: the kind of the latest call.
      if (kind.value !== 'month' && kind.value !== 'topic') { const last = all.value[all.value.length - 1]; kind.value = last ? kindOf(last[C.value.month]) : 'month'; }
      if (pick.value === null || (pick.value && !groups.value.some(g => g.owner === pick.value))) pick.value = groups.value[0]?.owner || '';
      await nextTick(); draw();
    }
    async function setSince(since, shown) {
      try { await api('/api/settings', { method: 'PUT', body: { usage_since: since, usage_shown: shown } }); pick.value = null; await load(); } catch (e) { fail(e); }
    }
    async function restart() {
      if (!await confirmBox('重新开始统计', '这一页会只统计从现在开始的调用，现有月报和专题已经用掉的量不再显示在这里。\n\n月度预算照常计算。之后可以在这一页顶部挑选要恢复的月报或专题，没挑的保持不计。',
                            { ok: '重新开始统计', danger: true, type: '重新开始统计' })) return;
      await setSince(Math.floor(Date.now() / 1000), []);
      toast('已从现在开始重新统计', 'ok');
    }
    function openRestore() { restore.value = []; }
    function toggleRestore(o) { const i = restore.value.indexOf(o); if (i < 0) restore.value.push(o); else restore.value.splice(i, 1); }
    async function doRestore() {
      const picked = restore.value, left = data.value.hidden.length - picked.length;
      restore.value = null;
      // With nothing left uncounted the page is back to counting everything.
      const shown = [...new Set([...(store.settings?.settings.usage_shown || []), ...picked])];
      await setSince(left ? data.value.since : null, left ? shown : []);
      toast(`已恢复 ${picked.length} 个的统计`, 'ok');
    }
    // One line per call for the selection (or all of this kind); Excel opens it directly.
    async function exportCsv() {
      const c = C.value, q = v => /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : v;
      const head = ['时间', kindName.value, '环节', '模型', '模型名', '成功', '输入 token', '输出 token', '缓存输入 token', '推理 token', '秒数', '费用', '币种'];
      const lines = [head.join(',')].concat(rows.value.map(r => [fmtTime(r[c.ts], true), name(r[c.month]), label(r[c.stage]), r[c.model_id], r[c.model], r[c.ok] ? '是' : '否',
        r[c.in], r[c.out], r[c.cached], r[c.reasoning], r[c.seconds], r[c.cost] === null ? '' : r[c.cost].toFixed(6), r[c.currency] || ''].map(q).join(',')));
      const text = lines.join('\r\n') + '\r\n';
      const file = `消息月报用量_${kindName.value}_${pick.value ? name(pick.value) : '全部'}.csv`.replace(/[\\/:*?"<>|]/g, '_');
      if (window.pywebview?.api?.save_text) {
        try { const path = await window.pywebview.api.save_text(file, text); if (path) toast('已导出：' + path, 'ok'); } catch (e) { fail(e); }
      } else {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv' })); a.download = file; a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      }
    }
    async function setBudget() {
      try { await api('/api/settings', { method: 'PUT', body: { budget_tokens: suggest.value } }); toast(`每月预算已设为 ${fmtInt(suggest.value)} token`, 'ok'); loadOverview(); } catch (e) { fail(e); }
    }
    watch([rows, budget, kind], async () => { await nextTick(); draw(); });
    const resize = () => { Object.values(charts).forEach(ch => ch.resize()); draw(); };     // how many labels fit depends on the width
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const retheme = () => { Object.values(charts).forEach(ch => ch.dispose()); Object.keys(charts).forEach(k => delete charts[k]); draw(); };
    onMounted(() => { load(); loadSettings().catch(fail); window.addEventListener('resize', resize); mq.addEventListener('change', retheme); });
    onBeforeUnmount(() => { window.removeEventListener('resize', resize); mq.removeEventListener('change', retheme); Object.values(charts).forEach(ch => ch.dispose()); });
    return { data, kind, kindName, pick, all, kindRows, rows, total, sum, groups, table, insight, avg, suggest, budget, monthly, matrix, store, label, money, name, title,
             setKind, load, setBudget, restart, restore, openRestore, toggleRestore, doRestore, running, unfinished, exportCsv,
             fmtTime, fmtTok, fmtInt, pct, stateText, stateKind };
  },
};
