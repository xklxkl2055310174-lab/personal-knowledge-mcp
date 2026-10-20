<template>
  <div class="page">
    <header class="page-head">
      <div class="row">
        <h1>{{ server?.serverInfo?.title || 'Personal Knowledge MCP' }}</h1>
        <span v-if="server" class="badge green">v{{ server.serverInfo.version }}</span>
        <span v-if="server" class="badge blue">MCP {{ server.protocolVersion }}</span>
      </div>
      <p>{{ server?.serverInfo?.description || '把本地笔记检索 + Git 仓库查询 + CI 状态查询封装成 MCP 工具，交给 Agent 调用。' }}</p>
    </header>

    <div v-if="error" class="error-box">{{ error }}</div>

    <section class="grid grid-4">
      <div class="card stat"><span class="value">{{ tools.length || '–' }}</span><span class="label">MCP 工具</span></div>
      <div class="card stat"><span class="value">{{ overview?.notes_count ?? '–' }}</span><span class="label">知识库笔记</span></div>
      <div class="card stat"><span class="value">{{ overview?.metrics?.runs ?? '–' }}</span><span class="label">Agent 运行次数</span></div>
      <div class="card stat"><span class="value">{{ overview?.config?.allowed_commands?.length ?? '–' }}</span><span class="label">白名单命令</span></div>
    </section>

    <section class="card">
      <div class="card-head">
        <div>
          <h2>工具清单</h2>
          <p class="muted small">通过 MCP <code>tools/list</code> 实时发现，每个工具返回统一结构 <code>{ ok, data, error, meta }</code></p>
        </div>
      </div>
      <div class="tools">
        <article v-for="t in tools" :key="t.name" class="tool">
          <div class="row">
            <span class="tool-icon" aria-hidden="true">{{ meta(t.name).icon }}</span>
            <div>
              <h3 class="mono">{{ t.name }}</h3>
              <div class="muted small">{{ meta(t.name).label }}</div>
            </div>
            <span class="spacer"></span>
            <router-link :to="{ path: '/tools', query: { name: t.name } }" class="btn secondary small-btn">调试</router-link>
          </div>
          <p class="small">{{ t.description }}</p>
          <div class="row">
            <span v-for="p in params(t)" :key="p.name" class="badge" :class="p.required ? 'blue' : ''">
              {{ p.name }}<template v-if="p.type">: {{ p.type }}</template>{{ p.required ? ' *' : '' }}
            </span>
          </div>
          <div class="guard small"><span class="muted">关键保护：</span>{{ meta(t.name).guard }}</div>
        </article>
        <div v-if="!tools.length && !error" class="empty">正在发现工具…</div>
      </div>
    </section>

    <section class="grid grid-2">
      <div class="card">
        <h2 style="margin-bottom:16px">运行配置</h2>
        <table v-if="overview">
          <tr><th>MCP 端点</th><td class="mono">{{ endpoint }}</td></tr>
          <tr><th>知识库目录</th><td class="mono">{{ overview.config.knowledge_root }}</td></tr>
          <tr><th>工作区沙箱</th><td class="mono">{{ overview.config.workspace_root }}</td></tr>
          <tr><th>Git 仓库</th><td class="mono">{{ overview.config.repository_root }}</td></tr>
          <tr><th>命令白名单</th><td><span v-for="c in overview.config.allowed_commands" :key="c" class="badge mono" style="margin:0 4px 4px 0">{{ c }}</span></td></tr>
          <tr><th>命令超时</th><td>{{ overview.config.command_timeout_seconds }} s</td></tr>
          <tr><th>文件大小上限</th><td>{{ (overview.config.max_file_bytes / 1024).toFixed(0) }} KB</td></tr>
          <tr><th>GitHub Token</th><td>{{ overview.config.github_token_configured ? '已配置' : '未配置（仅公开仓库）' }}</td></tr>
        </table>
      </div>
      <div class="card stack">
        <h2>Server Instructions</h2>
        <p class="muted small">MCP <code>initialize</code> 返回给 Host 的使用约束</p>
        <pre class="block">{{ server?.instructions || '–' }}</pre>
        <h3 style="margin-top:8px">全链路</h3>
        <ol class="flow">
          <li><b>任务接入</b><span>用户问题 → KnowledgeAgent</span></li>
          <li><b>执行编排</b><span>检索子 Agent 经 MCP 调用 search_notes</span></li>
          <li><b>上下文管理</b><span>full / relevant / summary + token 预算</span></li>
          <li><b>结果回写</b><span>总结子 Agent 生成带出处回答，记录 token 指标</span></li>
        </ol>
        <router-link to="/agent" class="btn" style="align-self:flex-start">去运行 Agent →</router-link>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { initialize, listTools } from '../api/mcp'
import { api } from '../api/http'
import { TOOL_META } from '../meta'

const server = ref(null)
const tools = ref([])
const overview = ref(null)
const error = ref('')
const endpoint = computed(() => `${location.origin}/mcp`)

const meta = name => TOOL_META[name] || { label: '', guard: '-', icon: '•' }
const params = tool => {
  const schema = tool.inputSchema || {}
  const required = new Set(schema.required || [])
  return Object.entries(schema.properties || {}).map(([name, p]) => ({
    name,
    required: required.has(name),
    type: p.type || p.anyOf?.map(x => x.type).filter(t => t !== 'null').join('|') || ''
  }))
}

onMounted(async () => {
  try {
    server.value = await initialize()
    tools.value = await listTools()
  } catch (e) {
    error.value = `MCP 连接失败：${e.message}。请确认后端已启动（uv run knowledge-mcp）。`
  }
  try { overview.value = await api.overview() } catch { /* 已在上方提示 */ }
})
</script>

<style scoped>
.tools { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.tool { border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.tool-icon { width: 36px; height: 36px; border-radius: 8px; background: var(--soft); display: grid; place-items: center; font-size: 18px; }
.small-btn { min-height: 36px; font-size: 14px; }
.btn { text-decoration: none !important; }
.guard { padding-top: 10px; border-top: 1px dashed var(--border); }
table th { width: 120px; }
table td { word-break: break-all; }
.flow { margin: 0; padding: 0; list-style: none; counter-reset: s; display: flex; flex-direction: column; gap: 10px; }
.flow li { counter-increment: s; display: flex; gap: 10px; align-items: baseline; font-size: 14px; }
.flow li::before { content: counter(s); width: 22px; height: 22px; flex-shrink: 0; border-radius: 50%; background: var(--blue-soft); color: #1b67b3; font-size: 12px; display: grid; place-items: center; }
.flow b { font-weight: 600; white-space: nowrap; }
.flow span { color: var(--text-2); }
</style>
