<template>
  <div class="page">
    <header class="page-head">
      <h1>Agent 问答</h1>
      <p>检索子 Agent 通过 MCP 调用 <code>search_notes</code> 取证，上下文管理按策略与预算注入，总结子 Agent 只基于证据回答。</p>
      <div class="row provider-status">
        <span class="badge" :class="providerState.configured ? 'green' : ''">
          {{ providerState.configured ? '真实 LLM' : '本地演示' }}
        </span>
        <span class="small muted">当前回答模型：{{ providerLabel }}</span>
      </div>
    </header>

    <section class="card stack">
      <label class="field">
        问题
        <textarea v-model="query" rows="2" maxlength="500" placeholder="例如：MCP 为什么适合连接 Agent 和工具？" @keydown.ctrl.enter="ask" @keydown.meta.enter="ask"></textarea>
      </label>
      <div class="row">
        <span class="muted small">试试：</span>
        <button v-for="s in samples" :key="s" class="chip" @click="query = s">{{ s }}</button>
      </div>
      <div class="controls">
        <div class="field">
          <span>上下文策略</span>
          <div class="segmented" role="radiogroup" aria-label="上下文策略">
            <button v-for="s in STRATEGIES" :key="s.value" role="radio" :aria-checked="strategy === s.value"
                    :class="{ active: strategy === s.value }" :title="s.desc" @click="strategy = s.value">{{ s.label }}</button>
          </div>
          <span class="small muted">{{ STRATEGIES.find(s => s.value === strategy).desc }}</span>
        </div>
        <label class="field budget">
          <span>Token 预算：<b class="mono">{{ budget }}</b></span>
          <input v-model.number="budget" type="range" min="64" max="2000" step="16" />
        </label>
        <div class="row actions">
          <button :disabled="loading || !query.trim()" @click="ask">{{ loading === 'ask' ? '运行中…' : '运行 Agent' }}</button>
          <button class="secondary" :disabled="loading || !query.trim()" @click="compare">{{ loading === 'compare' ? '对比中…' : '三种策略对比' }}</button>
        </div>
      </div>
      <div v-if="error" class="error-box">{{ error }}</div>
    </section>

    <template v-if="trace">
      <section class="card">
        <div class="card-head">
          <h2>执行链路</h2>
          <span class="badge" :class="trace.success ? 'green' : 'orange'">{{ trace.success ? '找到证据' : '无证据' }} · 共 {{ trace.total_ms }} ms</span>
        </div>
        <ol class="pipeline">
          <li v-for="(s, i) in trace.steps" :key="s.name" :class="{ active: activeStep === i }" @click="activeStep = i">
            <span class="idx">{{ i + 1 }}</span>
            <span class="title">{{ s.title }}</span>
            <span class="muted small">{{ s.duration_ms }} ms</span>
          </li>
        </ol>
        <div class="step-detail">
          <JsonView :value="trace.steps[activeStep].detail" />
        </div>
      </section>

      <section class="grid grid-2">
        <div class="card stack">
          <h2>回答</h2>
          <pre class="block answer">{{ trace.answer }}</pre>
          <h3>证据（{{ trace.evidence.length }}）</h3>
          <ul class="evidence">
            <li v-for="e in evidence" :key="e.raw">
              <router-link class="badge blue mono" :to="{ path: '/files', query: { note: e.path, line: e.line } }">{{ e.path }}:{{ e.line }}</router-link>
              <span class="small">{{ e.snippet }}</span>
            </li>
            <li v-if="!evidence.length" class="muted small">知识库中没有命中内容，可以在「笔记与工作区」补充笔记后重试。</li>
          </ul>
        </div>
        <div class="card stack">
          <h2>上下文预算</h2>
          <div class="bars">
            <div class="bar-row">
              <span class="small">检索原文</span>
              <div class="bar"><div class="fill gray" :style="{ width: pct(trace.context.original_tokens) }"></div></div>
              <span class="mono small">{{ trace.context.original_tokens }}</span>
            </div>
            <div class="bar-row">
              <span class="small">实际注入</span>
              <div class="bar"><div class="fill blue" :style="{ width: pct(trace.context.estimated_tokens) }"></div></div>
              <span class="mono small">{{ trace.context.estimated_tokens }}</span>
            </div>
            <div class="bar-row">
              <span class="small">预算上限</span>
              <div class="bar"><div class="fill outline" :style="{ width: pct(budgetUsed) }"></div></div>
              <span class="mono small">{{ budgetUsed }}</span>
            </div>
          </div>
          <div class="row">
            <span class="badge blue">策略 {{ trace.strategy }}</span>
            <span class="badge" :class="trace.context.truncated ? 'orange' : 'green'">{{ trace.context.truncated ? '已截断 / 压缩' : '未截断' }}</span>
            <span class="badge">节省 {{ (trace.context.saved_ratio * 100).toFixed(1) }}%</span>
          </div>
          <details>
            <summary class="small">查看注入给总结 Agent 的上下文</summary>
            <pre class="block" style="margin-top:8px">{{ trace.context.text || '（空）' }}</pre>
          </details>
        </div>
      </section>
    </template>

    <section v-if="comparison" class="card">
      <div class="card-head">
        <div>
          <h2>策略对比</h2>
          <p class="muted small">同一问题、同一预算（{{ comparison.budget }} tokens）下三种上下文策略的成本</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <tr><th>策略</th><th>输入 token</th><th style="width:40%">相对原文</th><th>输出 token</th><th>截断</th><th>成功</th><th>耗时</th></tr>
          <tr v-for="r in comparison.rows" :key="r.strategy">
            <td><span class="badge blue mono">{{ r.strategy }}</span></td>
            <td class="mono">{{ r.input_tokens }} / {{ r.original_tokens }}</td>
            <td><div class="bar"><div class="fill blue" :style="{ width: ratio(r) }"></div></div></td>
            <td class="mono">{{ r.output_tokens }}</td>
            <td>{{ r.truncated ? '是' : '否' }}</td>
            <td>{{ r.success ? '✓' : '✕' }}</td>
            <td class="mono">{{ r.total_ms }} ms</td>
          </tr>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <div>
          <h2>历史运行指标</h2>
          <p class="muted small">来自 <code>.runtime/agent_metrics.jsonl</code>，每次运行自动回写</p>
        </div>
        <button class="secondary" @click="loadMetrics">刷新</button>
      </div>
      <div class="table-wrap" v-if="metricRows.length">
        <table>
          <tr><th>策略</th><th>运行次数</th><th>成功率</th><th>平均输入 token</th><th>平均输出 token</th></tr>
          <tr v-for="m in metricRows" :key="m.strategy">
            <td><span class="badge blue mono">{{ m.strategy }}</span></td>
            <td class="mono">{{ m.runs }}</td>
            <td class="mono">{{ (m.success_rate * 100).toFixed(0) }}%</td>
            <td class="mono">{{ (m.input_tokens / m.runs).toFixed(1) }}</td>
            <td class="mono">{{ (m.output_tokens / m.runs).toFixed(1) }}</td>
          </tr>
        </table>
      </div>
      <div v-else class="empty">还没有运行记录</div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/http'
import { STRATEGIES } from '../meta'
import { providerLabel, providerPayload, providerState } from '../provider-state'
import JsonView from '../components/JsonView.vue'

const samples = ['MCP 为什么适合连接 Agent 和工具？', '上下文管理策略 token 成本', '哪些失败需要重试', '子 Agent 委派的优缺点']
const query = ref(samples[0])
const strategy = ref('relevant')
const budget = ref(800)
const loading = ref('')
const error = ref('')
const trace = ref(null)
const comparison = ref(null)
const activeStep = ref(1)
const metrics = ref(null)

const budgetUsed = computed(() => trace.value?.steps[0].detail.budget ?? budget.value)
const scale = computed(() => Math.max(trace.value?.context.original_tokens || 0, budgetUsed.value, 1))
const pct = v => `${Math.min(100, (v / scale.value) * 100)}%`
const ratio = r => `${r.original_tokens ? Math.min(100, (r.input_tokens / r.original_tokens) * 100) : 0}%`

const evidence = computed(() =>
  (trace.value?.evidence || []).map(raw => {
    const m = raw.match(/^(.*?):(\d+) \| (.*)$/s)
    return m ? { raw, path: m[1], line: Number(m[2]), snippet: m[3] } : { raw, path: raw, line: 1, snippet: '' }
  })
)
const metricRows = computed(() =>
  Object.entries(metrics.value?.by_strategy || {}).map(([strategy, v]) => ({ strategy, ...v }))
)

const run = async (kind, fn) => {
  loading.value = kind
  error.value = ''
  try { await fn() } catch (e) { error.value = e.message } finally { loading.value = '' }
  loadMetrics()
}
const ask = () => run('ask', async () => {
  trace.value = await api.ask({
    query: query.value,
    strategy: strategy.value,
    max_tokens: budget.value,
    provider: providerPayload()
  })
  activeStep.value = 1
})
const compare = () => run('compare', async () => {
  comparison.value = await api.compare({
    query: query.value,
    max_tokens: budget.value,
    provider: providerPayload()
  })
  trace.value = comparison.value.traces.find(t => t.strategy === strategy.value)
})
const loadMetrics = async () => { try { metrics.value = await api.metrics() } catch { /* ignore */ } }
onMounted(loadMetrics)
</script>

<style scoped>
.controls { display: grid; grid-template-columns: auto minmax(200px, 1fr) auto; gap: 24px; align-items: end; }
.provider-status { margin-top: 6px; }
.actions { justify-content: flex-end; }
.pipeline { list-style: none; margin: 0 0 16px; padding: 0; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.pipeline li {
  position: relative; display: flex; flex-direction: column; gap: 4px; padding: 12px; border: 1px solid var(--border);
  border-radius: var(--radius); cursor: pointer; min-height: 44px; background: var(--canvas);
}
.pipeline li:hover { border-color: var(--blue); }
.pipeline li.active { border-color: var(--blue); background: var(--blue-soft); }
.pipeline .idx { width: 22px; height: 22px; border-radius: 50%; background: var(--blue); color: #fff; font-size: 12px; display: grid; place-items: center; }
.pipeline .title { font-size: 14px; font-weight: 500; }
.answer { max-height: 320px; }
.evidence { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 10px; }
.evidence li { display: flex; gap: 8px; align-items: baseline; }
.evidence a { flex-shrink: 0; }
.bars { display: flex; flex-direction: column; gap: 12px; }
.bar-row { display: grid; grid-template-columns: 64px 1fr 48px; gap: 12px; align-items: center; }
.bar { height: 10px; border-radius: 5px; background: var(--surface-2); overflow: hidden; }
.fill { height: 100%; border-radius: 5px; transition: width .3s; }
.fill.blue { background: var(--blue); }
.fill.gray { background: #b9b6b0; }
.fill.outline { background: repeating-linear-gradient(45deg, #cfe4f8 0 6px, #e5f2fc 6px 12px); }
details summary { cursor: pointer; color: var(--blue); }
@media (prefers-reduced-motion: reduce) { .fill { transition: none; } }
@media (max-width: 960px) {
  .controls { grid-template-columns: 1fr; }
  .actions { justify-content: flex-start; }
  .pipeline { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
