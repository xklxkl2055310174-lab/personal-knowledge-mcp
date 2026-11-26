<template>
  <div class="page">
    <header class="page-head">
      <h1>Agent 问答</h1>
      <p v-if="mode === 'delegated' && delegationAvailable">协调 Agent 先拆解任务，再委派给知识、文件、Git、CI 或命令子 Agent；每个子 Agent 只能看到自己的 MCP 工具。</p>
      <p v-else-if="mode === 'delegated'">当前未连接支持工具调用的模型，因此运行离线知识问答链；连接模型后自动启用真实多 Agent 委派。</p>
      <p v-else>单 Agent 先通过 <code>tools/list</code> 发现五个 MCP 工具，再由模型根据任务选择并调用；所有执行仍经过 MCP Client。</p>
      <div class="row provider-status">
        <span class="badge" :class="providerState.configured ? 'green' : ''">
          {{ providerState.configured ? '真实 LLM' : '本地演示' }}
        </span>
        <span class="small muted">当前回答模型：{{ providerLabel }}</span>
      </div>
    </header>

    <details class="card trace-details">
      <summary class="trace-summary">
        <span class="trace-title">Agent 问答</span>
        <span class="badge blue">{{ MODES.find(item => item.value === mode).label }}</span>
        <span class="muted small">记忆 {{ history.length }} 条</span>
        <span class="spacer"></span>
        <span class="trace-chevron" aria-hidden="true">⌄</span>
      </summary>
      <div class="trace-expanded">
      <div class="row memory-bar">
        <span class="badge green">会话记忆 {{ history.length }} 条</span>
        <span class="small muted">最近对话保存在本机 SQLite，并按 Token 预算注入</span>
        <span class="spacer"></span>
        <button type="button" class="secondary" :disabled="loading" @click="newConversation">新对话</button>
        <button type="button" class="secondary" :disabled="loading || !history.length" @click="clearMemory">清空记忆</button>
      </div>
      <div v-if="history.length" class="conversation-history" role="log" aria-label="当前会话记忆">
        <div v-for="message in history" :key="message.id" class="memory-message" :class="message.role">
          <b>{{ message.role === 'user' ? '问题' : '回答' }}</b>
          <span>{{ message.content }}</span>
        </div>
      </div>
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
          <span>Agent 模式</span>
          <div class="segmented" role="radiogroup" aria-label="Agent 模式">
            <button v-for="item in MODES" :key="item.value" role="radio"
                    :aria-checked="mode === item.value" :class="{ active: mode === item.value }"
                    :disabled="item.value === 'single' && !singleAvailable"
                    :title="item.value === 'single' && !singleAvailable ? '请先连接支持工具调用的真实模型' : item.desc"
                    @click="mode = item.value">{{ item.label }}</button>
          </div>
          <span class="small muted">{{ MODES.find(item => item.value === mode).desc }}</span>
        </div>
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
          <button class="secondary" :disabled="loading || !query.trim() || mode === 'single'" title="策略对比使用固定知识问答链，不比较协调器路由" @click="compare">{{ loading === 'compare' ? '对比中…' : '三种策略对比' }}</button>
        </div>
      </div>
      <div v-if="error" class="error-box">{{ error }}</div>
      </div>
    </details>

    <template v-if="trace">
      <details class="card trace-details">
        <summary class="trace-summary">
          <span class="trace-title">执行链路</span>
          <span class="badge blue">{{ trace.mode === 'single' ? '自主工具' : '多 Agent 委派' }}</span>
          <span class="muted small">{{ trace.steps.length }} 个步骤</span>
          <span class="spacer"></span>
          <span class="badge" :class="trace.success ? 'green' : 'orange'">
            {{ trace.success ? '执行成功' : '需要检查' }}
          </span>
          <span class="mono small muted">{{ trace.total_ms }} ms</span>
          <span class="trace-chevron" aria-hidden="true">⌄</span>
        </summary>
        <div class="trace-expanded">
          <ol class="pipeline">
            <li v-for="(s, i) in trace.steps" :key="s.name" :class="{ active: activeStep === i }" @click="activeStep = i">
              <span class="idx">{{ i + 1 }}</span>
              <span class="title">{{ s.title }}</span>
              <span class="muted small">{{ s.duration_ms }} ms</span>
            </li>
          </ol>
          <div class="step-detail">
            <div v-if="searchCalls.length" class="search-context-list">
              <h3>search_notes 查询与实际注入上下文</h3>
              <article v-for="(call, index) in searchCalls" :key="index" class="search-context-card">
                <div class="row">
                  <span class="badge blue">查询 {{ index + 1 }}</span>
                  <code>{{ call.arguments?.query || '（空查询）' }}</code>
                  <span class="spacer"></span>
                  <span class="badge" :class="call.called && call.ok ? 'green' : 'orange'">
                    {{ call.called ? (call.ok ? '调用成功' : '调用失败') : '未执行' }}
                  </span>
                </div>
                <div class="row small muted">
                  <span>top_k={{ call.arguments?.top_k }}</span>
                  <span>唯一命中 {{ call.unique_matches }}</span>
                  <span>去重 {{ call.duplicates_removed }}</span>
                  <span>上下文 {{ call.context?.estimated_tokens }} / {{ call.context?.original_tokens }} tokens</span>
                  <span>{{ call.context?.truncated ? '已裁剪' : '未裁剪' }}</span>
                  <span v-if="call.call_limit">上限 {{ call.call_limit }} 次</span>
                </div>
                <pre class="block search-context">{{ call.context?.text || '（没有可注入的新证据）' }}</pre>
              </article>
            </div>
            <JsonView :value="activeDetail" />
          </div>
        </div>
      </details>

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
          <h2>上下文 / 工具结果预算</h2>
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
            <span v-if="trace.memory" class="badge green">记忆 {{ trace.memory.injected_messages }} 条 / {{ trace.memory.estimated_tokens }} tokens</span>
          </div>
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
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/http'
import { STRATEGIES } from '../meta'
import { providerLabel, providerPayload, providerState } from '../provider-state'
import JsonView from '../components/JsonView.vue'

const samples = ['MCP 为什么适合连接 Agent 和工具？', '查看当前 Git 分支和未提交改动', '读取本地 CI 状态并解释', '列出 examples 目录并总结文件']
const MODES = [
  { value: 'delegated', label: '多 Agent 委派', desc: '协调器按任务选择最小权限的知识、文件、Git、CI 或命令子 Agent' },
  { value: 'single', label: '自主工具', desc: '模型可从五个 MCP 工具中自主选择并组合调用' }
]
const query = ref(samples[0])
const mode = ref('delegated')
const strategy = ref('relevant')
const budget = ref(800)
const loading = ref('')
const error = ref('')
const trace = ref(null)
const comparison = ref(null)
const activeStep = ref(1)
const metrics = ref(null)
const MEMORY_KEY = 'knowledge-mcp-conversation-id'
const makeConversationId = () => {
  const random = globalThis.crypto?.randomUUID?.().replaceAll('-', '')
  return random || `local_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}
const conversationId = ref(localStorage.getItem(MEMORY_KEY) || makeConversationId())
localStorage.setItem(MEMORY_KEY, conversationId.value)
const history = ref([])
const singleAvailable = computed(() => providerState.configured && providerState.supports_tools)
const delegationAvailable = computed(() => providerState.configured && providerState.supports_tools)
watch(singleAvailable, available => { if (!available && mode.value === 'single') mode.value = 'delegated' })

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
const activeDetail = computed(() => trace.value?.steps?.[activeStep.value]?.detail || {})
const searchCalls = computed(() =>
  (activeDetail.value.tool_call_log || []).filter(item => item.tool === 'search_notes')
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
    mode: mode.value,
    conversation_id: conversationId.value,
    provider: providerPayload()
  })
  activeStep.value = trace.value.steps.length > 1 ? 1 : 0
  await loadConversation()
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
const loadConversation = async () => {
  try { history.value = (await api.conversation(conversationId.value)).messages || [] } catch { history.value = [] }
}
const newConversation = () => {
  conversationId.value = makeConversationId()
  localStorage.setItem(MEMORY_KEY, conversationId.value)
  history.value = []
  trace.value = null
  comparison.value = null
}
const clearMemory = () => run('memory', async () => {
  await api.clearConversation(conversationId.value)
  history.value = []
  trace.value = null
})
onMounted(() => { loadMetrics(); loadConversation() })
</script>

<style scoped>
.controls { display: grid; grid-template-columns: auto minmax(200px, 1fr) auto; gap: 24px; align-items: end; }
.provider-status { margin-top: 6px; }
.memory-bar { gap: 8px; }
.conversation-history { display: flex; flex-direction: column; gap: 8px; max-height: 260px; overflow: auto; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--canvas); }
.memory-message { display: grid; grid-template-columns: 56px 1fr; gap: 8px; white-space: pre-wrap; line-height: 1.55; }
.memory-message.user b { color: var(--blue); }
.memory-message.assistant b { color: var(--green); }
.actions { justify-content: flex-end; }
.trace-details { padding: 0; overflow: hidden; }
.trace-summary {
  display: flex; align-items: center; gap: 10px; min-height: 28px; padding: 16px 20px;
  cursor: pointer; list-style: none; color: var(--text);
}
.trace-summary::-webkit-details-marker { display: none; }
.trace-summary:focus-visible { outline: 2px solid var(--blue); outline-offset: -3px; }
.trace-title { font-size: 18px; font-weight: 650; }
.trace-chevron { font-size: 20px; line-height: 1; transition: transform .2s ease; }
.trace-details[open] .trace-chevron { transform: rotate(180deg); }
.trace-expanded { padding: 16px 20px 20px; border-top: 1px solid var(--border); }
.pipeline { list-style: none; margin: 0 0 16px; padding: 0; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.pipeline li {
  position: relative; display: flex; flex-direction: column; gap: 4px; padding: 12px; border: 1px solid var(--border);
  border-radius: var(--radius); cursor: pointer; min-height: 44px; background: var(--canvas);
}
.pipeline li:hover { border-color: var(--blue); }
.pipeline li.active { border-color: var(--blue); background: var(--blue-soft); }
.pipeline .idx { width: 22px; height: 22px; border-radius: 50%; background: var(--blue); color: #fff; font-size: 12px; display: grid; place-items: center; }
.pipeline .title { font-size: 14px; font-weight: 500; }
.search-context-list { display: flex; flex-direction: column; gap: 10px; margin-bottom: 16px; }
.search-context-list h3 { margin: 0; }
.search-context-card { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--canvas); }
.search-context { max-height: 240px; margin: 0; }
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
@media (prefers-reduced-motion: reduce) { .fill, .trace-chevron { transition: none; } }
@media (max-width: 960px) {
  .controls { grid-template-columns: 1fr; }
  .actions { justify-content: flex-start; }
  .pipeline { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .trace-summary { flex-wrap: wrap; }
}
</style>
