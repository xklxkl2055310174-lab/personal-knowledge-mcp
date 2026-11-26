<template>
  <div class="page">
    <header class="page-head">
      <h1>工具调试台</h1>
      <p>浏览器直接作为 MCP Client，通过 JSON-RPC <code>tools/call</code> 调用工具，观察参数校验、权限边界、失败分类与 Verification Loop。</p>
    </header>

    <div class="layout">
      <nav class="tool-list card" aria-label="工具">
        <button v-for="t in toolNames" :key="t" class="tool-btn" :class="{ active: name === t }" @click="select(t)">
          <span aria-hidden="true">{{ TOOL_META[t].icon }}</span>
          <span class="stackless">
            <span class="mono">{{ t }}</span>
            <span class="muted small">{{ TOOL_META[t].label }}</span>
          </span>
        </button>
      </nav>

      <div class="stack" style="min-width:0">
        <section class="card stack">
          <div class="card-head" style="margin-bottom:0">
            <div>
              <h2 class="mono">{{ name }}</h2>
              <p class="muted small">{{ description }}</p>
            </div>
          </div>

          <!-- search_notes -->
          <div v-if="name === 'search_notes'" class="grid form-grid">
            <label class="field wide">检索问题<input v-model="forms.search_notes.query" /></label>
            <label class="field">最大返回数量（1-20）<input v-model.number="forms.search_notes.top_k" type="number" /></label>
          </div>

          <!-- file_access -->
          <div v-else-if="name === 'file_access'" class="grid form-grid">
            <label class="field">操作类型
              <select v-model="forms.file_access.action">
                <option value="list">列出目录</option>
                <option value="read">读取文件</option>
                <option value="write">写入文件</option>
                <option value="replace">局部替换</option>
              </select>
            </label>
            <div class="field wide">
              <label for="file-access-path">工作区相对路径</label>
              <div class="path-picker-row">
                <input id="file-access-path" v-model="forms.file_access.path" />
                <button type="button" class="secondary" @click="openFilePicker">浏览…</button>
              </div>
            </div>
            <label v-if="forms.file_access.action === 'write'" class="field">写入模式
              <select v-model="forms.file_access.mode">
                <option value="create">仅新建</option>
                <option value="overwrite">覆盖现有文件</option>
              </select>
            </label>
            <label v-if="['write', 'replace'].includes(forms.file_access.action)" class="field wide">
              预期文件哈希（可选，从读取结果复制）
              <input v-model.trim="forms.file_access.expected_sha256" class="mono" autocomplete="off" />
            </label>
            <label v-if="forms.file_access.action === 'write'" class="field full">写入内容
              <textarea v-model="forms.file_access.content" class="code" rows="4"></textarea>
            </label>
            <label v-if="forms.file_access.action === 'replace'" class="field full">需要替换的原文本（必须唯一命中一次）
              <textarea v-model="forms.file_access.old_text" class="code" rows="4"></textarea>
            </label>
            <label v-if="forms.file_access.action === 'replace'" class="field full">替换后的新文本
              <textarea v-model="forms.file_access.new_text" class="code" rows="4"></textarea>
            </label>
          </div>

          <!-- git_query -->
          <div v-else-if="name === 'git_query'" class="grid form-grid">
            <label class="field">查询类型
              <select v-model="forms.git_query.operation">
                <option value="status">工作区状态</option>
                <option value="branch">当前分支</option>
                <option value="log">提交记录</option>
                <option value="diff">代码差异</option>
              </select>
            </label>
            <label class="field">仓库相对路径<input v-model="forms.git_query.repo_path" /></label>
            <label class="field">最大返回数量（1-50）<input v-model.number="forms.git_query.limit" type="number" /></label>
          </div>

          <!-- ci_status -->
          <div v-else-if="name === 'ci_status'" class="grid form-grid">
            <label class="field">状态来源
              <select v-model="forms.ci_status.source">
                <option value="local">本地记录</option>
                <option value="github">GitHub Actions</option>
              </select>
            </label>
            <label v-if="forms.ci_status.source === 'github'" class="field">GitHub 仓库（所有者/仓库名）<input v-model="forms.ci_status.repository" placeholder="astral-sh/uv" /></label>
            <label class="field">最大返回数量（1-20）<input v-model.number="forms.ci_status.limit" type="number" /></label>
          </div>

          <!-- run_command -->
          <div v-else-if="name === 'run_command'" class="grid form-grid">
            <label class="field full">命令（按空格拆分为参数数组，支持引号）
              <input v-model="forms.run_command.command" class="mono" />
            </label>
            <div class="field wide">
              <label for="run-command-cwd">工作目录（相对安全工作区）</label>
              <div class="path-picker-row">
                <input id="run-command-cwd" v-model="forms.run_command.cwd" />
                <button type="button" class="secondary" @click="openCwdPicker">浏览…</button>
              </div>
            </div>
            <label class="field">超时时间（秒，最大30）<input v-model.number="forms.run_command.timeout_seconds" type="number" step="0.5" /></label>
          </div>

          <div class="row">
            <button :disabled="loading" @click="call">{{ loading ? '调用中…' : '调用工具' }}</button>
            <span class="muted small">示例：</span>
            <button v-for="p in presets[name]" :key="p.label" class="chip" @click="applyPreset(p)">{{ p.label }}</button>
          </div>

          <details>
            <summary class="small">JSON-RPC 请求预览</summary>
            <JsonView :value="preview" style="margin-top:8px" />
          </details>
        </section>

        <section class="card">
          <div class="card-head">
            <h2>返回结果</h2>
            <div class="segmented" v-if="current">
              <button :class="{ active: view === 'result' }" @click="view = 'result'">ToolResult</button>
              <button :class="{ active: view === 'raw' }" @click="view = 'raw'">原始 JSON-RPC</button>
            </div>
          </div>
          <div v-if="callError" class="error-box">{{ callError }}</div>
          <template v-else-if="current">
            <ToolResultView v-if="view === 'result'" :result="current.structured" :duration="current.trace.duration" />
            <div v-else class="stack">
              <div class="small muted">请求</div><JsonView :value="current.trace.request" />
              <div class="small muted">响应</div><JsonView :value="current.trace.response" />
            </div>
          </template>
          <div v-else class="empty">选择工具并点击「调用工具」</div>
        </section>

        <section v-if="history.length" class="card">
          <div class="card-head"><h2>调用历史</h2><button class="ghost" @click="history.splice(0)">清空</button></div>
          <div class="table-wrap">
            <table>
              <tr><th>时间</th><th>工具</th><th>参数</th><th>结果</th><th>耗时</th></tr>
              <tr v-for="(h, i) in history" :key="i" class="clickable" @click="current = h; view = 'result'">
                <td class="mono small">{{ h.time }}</td>
                <td class="mono">{{ h.name }}</td>
                <td class="mono small args">{{ JSON.stringify(h.args) }}</td>
                <td>
                  <span class="badge" :class="h.structured?.ok ? 'green' : 'red'">
                    {{ h.structured?.ok ? 'ok' : h.structured?.error?.category || 'error' }}
                  </span>
                </td>
                <td class="mono small">{{ h.trace.duration }} ms</td>
              </tr>
            </table>
          </div>
        </section>
      </div>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="filePicker.open" class="file-picker-backdrop" @click.self="closeFilePicker">
      <section
        ref="filePickerDialog"
        class="file-picker-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="file-picker-title"
        tabindex="-1"
        @keydown.esc="closeFilePicker"
      >
        <header class="file-picker-head">
          <div>
            <h2 id="file-picker-title">{{ filePicker.purpose === 'cwd' ? '选择命令工作目录' : '选择安全工作区路径' }}</h2>
            <p class="muted small">仅通过 MCP <code>file_access</code> 浏览，不会显示服务器绝对路径。</p>
          </div>
          <button type="button" class="icon-button" aria-label="关闭文件选择器" @click="closeFilePicker">×</button>
        </header>

        <div class="picker-toolbar">
          <button type="button" class="secondary" :disabled="filePicker.current === '.' || filePicker.loading" @click="goPickerParent">
            返回上级
          </button>
          <span class="picker-current small">当前相对路径：<code>{{ filePicker.current }}</code></span>
        </div>

        <div v-if="filePicker.loading" class="picker-state" role="status">正在加载目录…</div>
        <div v-else-if="filePicker.error" class="error-box" role="alert">{{ filePicker.error }}</div>
        <ul v-else-if="filePicker.entries.length" class="picker-list" aria-label="当前目录内容">
          <li v-for="entry in filePicker.entries" :key="`${entry.type}:${entry.name}`">
            <button
              type="button"
              class="picker-entry"
              :disabled="entry.type === 'file' && (filePicker.purpose === 'cwd' || forms.file_access.action === 'list')"
              :aria-label="pickerEntryLabel(entry)"
              @click="choosePickerEntry(entry)"
            >
              <span class="picker-entry-icon" aria-hidden="true">{{ entry.type === 'directory' ? '▸' : '·' }}</span>
              <span class="picker-entry-name">{{ entry.name }}</span>
              <span class="muted small">{{ entry.type === 'directory' ? '目录' : formatBytes(entry.size) }}</span>
            </button>
          </li>
        </ul>
        <div v-else class="picker-state muted">当前目录为空</div>

        <div v-if="filePicker.purpose === 'file' && forms.file_access.action === 'write'" class="picker-write">
          <p class="picker-write-hint small">
            点击上方已有文件可直接选择并返回；也可以在当前目录输入文件名，创建新文件或覆盖同名文件。
          </p>
          <label class="field">文件名
            <input
              v-model="filePicker.filename"
              placeholder="例如 agent-summary.md"
              @input="filePicker.filenameError = ''"
              @keydown.enter.prevent="chooseWriteTarget"
            />
          </label>
          <div v-if="filePicker.filenameError" class="error-box" role="alert">{{ filePicker.filenameError }}</div>
        </div>

        <footer class="picker-actions">
          <button
            v-if="filePicker.purpose === 'cwd' || forms.file_access.action === 'list'"
            type="button"
            :disabled="filePicker.loading || !!filePicker.error"
            @click="chooseCurrentDirectory"
          >
            选择当前目录
          </button>
          <button
            v-if="filePicker.purpose === 'file' && forms.file_access.action === 'write'"
            type="button"
            :disabled="filePicker.loading || !!filePicker.error"
            @click="chooseWriteTarget"
          >
            使用当前目录和文件名
          </button>
          <span class="spacer"></span>
          <button type="button" class="secondary" @click="closeFilePicker">关闭</button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { callTool, listTools } from '../api/mcp'
import { TOOL_META } from '../meta'
import JsonView from '../components/JsonView.vue'
import ToolResultView from '../components/ToolResultView.vue'

const toolNames = Object.keys(TOOL_META)
const route = useRoute()
const name = ref(toolNames.includes(route.query.name) ? route.query.name : 'search_notes')
const descriptions = ref({})
const loading = ref(false)
const current = ref(null)
const callError = ref('')
const view = ref('result')
const history = reactive([])
const filePickerDialog = ref(null)
const filePicker = reactive({
  open: false,
  purpose: 'file',
  current: '.',
  entries: [],
  loading: false,
  error: '',
  filename: '',
  filenameError: ''
})

const forms = reactive({
  search_notes: { query: 'MCP Agent', top_k: 5 },
  file_access: {
    action: 'list', path: '.', content: 'hello from web console\n', mode: 'overwrite',
    expected_sha256: '', old_text: '', new_text: ''
  },
  git_query: { operation: 'status', repo_path: '.', limit: 10 },
  ci_status: { source: 'local', repository: '', limit: 5 },
  run_command: { command: `python -c "print('MCP command OK')"`, cwd: '.', timeout_seconds: 10 }
})

const presets = {
  search_notes: [
    { label: '正常检索', form: { query: '上下文 token', top_k: 3 } },
    { label: '空查询 → parameter', form: { query: '   ', top_k: 5 } },
    { label: 'top_k 越界', form: { query: 'MCP', top_k: 99 } }
  ],
  file_access: [
    { label: '写入并校验', form: { action: 'write', path: 'web/demo.md', content: '# 来自 Web 控制台\n', mode: 'overwrite', expected_sha256: '' } },
    { label: '读取', form: { action: 'read', path: 'web/demo.md' } },
    { label: '局部替换', form: { action: 'replace', path: 'web/demo.md', old_text: '来自 Web 控制台', new_text: '来自 MCP replace', expected_sha256: '' } },
    { label: '哈希冲突', form: { action: 'write', path: 'web/demo.md', content: '# 不应覆盖\n', mode: 'overwrite', expected_sha256: '0000000000000000000000000000000000000000000000000000000000000000' } },
    { label: '列出演示目录', form: { action: 'list', path: 'examples' } },
    { label: '读取项目介绍', form: { action: 'read', path: 'examples/project-overview.md' } },
    { label: '读取安全说明', form: { action: 'read', path: 'examples/file-security.md' } },
    { label: '路径越权 → permission', form: { action: 'read', path: '../pyproject.toml' } }
  ],
  git_query: [
    { label: '最近提交', form: { operation: 'log', repo_path: '.', limit: 5 } },
    { label: '仓库外 → permission', form: { operation: 'status', repo_path: '../..', limit: 10 } }
  ],
  ci_status: [
    { label: '本地 CI', form: { source: 'local', repository: '', limit: 5 } },
    { label: 'GitHub Actions', form: { source: 'github', repository: 'astral-sh/uv', limit: 3 } },
    { label: '仓库名非法', form: { source: 'github', repository: 'not a repo', limit: 3 } }
  ],
  run_command: [
    { label: 'python 版本', form: { command: 'python --version', cwd: '.', timeout_seconds: 10 } },
    { label: '非白名单 → permission', form: { command: 'bash -c "rm -rf /"', cwd: '.', timeout_seconds: 10 } },
    { label: '超时 → timeout', form: { command: `python -c "import time; time.sleep(5)"`, cwd: '.', timeout_seconds: 1 } }
  ]
}

const description = computed(() => (
  TOOL_META[name.value].description || descriptions.value[name.value] || TOOL_META[name.value].guard
))

const normalizePickerPath = value => {
  const raw = String(value ?? '').trim().replace(/\\/g, '/')
  if (!raw || raw === '.') return '.'
  if (raw.startsWith('/') || /^[a-zA-Z]:/.test(raw)) return null
  const parts = []
  for (const part of raw.split('/')) {
    if (!part || part === '.') continue
    if (part === '..') return null
    parts.push(part)
  }
  return parts.join('/') || '.'
}

const pickerParent = path => {
  if (path === '.') return '.'
  const parts = path.split('/')
  parts.pop()
  return parts.join('/') || '.'
}

const joinPickerPath = (directory, name) => directory === '.' ? name : `${directory}/${name}`

const validEntryName = name => (
  typeof name === 'string' && name !== '' && name !== '.' && name !== '..' && !/[\\/]/.test(name)
)

const filenameProblem = value => {
  const filename = value.trim()
  if (!filename) return '请输入文件名。'
  if (filename === '.' || filename === '..' || /[\\/]/.test(filename)) return '文件名不能包含路径或路径穿越。'
  if (/[<>:"|?*\u0000-\u001f]/.test(filename) || /[. ]$/.test(filename)) return '文件名包含当前系统不支持的字符。'
  return ''
}

const formatBytes = value => {
  const size = Number(value)
  if (!Number.isFinite(size)) return '文件'
  if (size < 1024) return `${size} B`
  return `${(size / 1024).toFixed(1)} KB`
}

const pickerEntryLabel = entry => {
  if (entry.type === 'directory') return `进入目录 ${entry.name}`
  if (filePicker.purpose === 'cwd') return `${entry.name} 是文件，不能作为命令工作目录`
  if (forms.file_access.action === 'write') return `选择已有文件作为写入目标 ${entry.name}`
  if (forms.file_access.action === 'replace') return `选择文件进行局部替换 ${entry.name}`
  return `选择文件 ${entry.name}`
}

const loadPickerDirectory = async path => {
  const safePath = normalizePickerPath(path)
  if (!safePath) {
    filePicker.error = '文件选择器只接受安全工作区内的相对路径。'
    return false
  }
  filePicker.loading = true
  filePicker.error = ''
  try {
    const result = await callTool('file_access', { action: 'list', path: safePath })
    const structured = result.structured
    if (!structured?.ok) {
      const code = structured?.error?.code || 'list_failed'
      const message = structured?.error?.message || '无法列出目录'
      throw new Error(`${code}：${message}`)
    }
    filePicker.current = safePath
    filePicker.entries = (structured.data?.entries || []).filter(entry => (
      (entry.type === 'directory' || entry.type === 'file') && validEntryName(entry.name)
    ))
    return true
  } catch (error) {
    filePicker.entries = []
    filePicker.error = error.message || '无法列出目录'
    return false
  } finally {
    filePicker.loading = false
  }
}

const openFilePicker = async () => {
  filePicker.purpose = 'file'
  const action = forms.file_access.action
  const entered = normalizePickerPath(forms.file_access.path)
  let initial = '.'
  filePicker.filename = ''
  filePicker.filenameError = ''

  if (entered) {
    if (action === 'list') {
      initial = entered
    } else if (entered !== '.') {
      const parts = entered.split('/')
      const last = parts.pop()
      initial = parts.join('/') || '.'
      if (action === 'write' && last && !filenameProblem(last)) filePicker.filename = last
    }
  }

  filePicker.open = true
  await nextTick()
  filePickerDialog.value?.focus()
  const loaded = await loadPickerDirectory(initial)
  if (!loaded && initial !== '.') await loadPickerDirectory('.')
}

const openCwdPicker = async () => {
  filePicker.purpose = 'cwd'
  filePicker.filename = ''
  filePicker.filenameError = ''
  const initial = normalizePickerPath(forms.run_command.cwd) || '.'
  filePicker.open = true
  await nextTick()
  filePickerDialog.value?.focus()
  const loaded = await loadPickerDirectory(initial)
  if (!loaded && initial !== '.') await loadPickerDirectory('.')
}

const closeFilePicker = () => {
  filePicker.open = false
  filePicker.error = ''
  filePicker.filenameError = ''
}

const goPickerParent = () => loadPickerDirectory(pickerParent(filePicker.current))

const choosePickerEntry = entry => {
  if (!validEntryName(entry.name)) return
  const selected = joinPickerPath(filePicker.current, entry.name)
  if (entry.type === 'directory') {
    loadPickerDirectory(selected)
  } else if (
    filePicker.purpose === 'file' &&
    ['read', 'write', 'replace'].includes(forms.file_access.action)
  ) {
    forms.file_access.path = selected
    closeFilePicker()
  }
}

const chooseCurrentDirectory = () => {
  if (filePicker.purpose === 'cwd') forms.run_command.cwd = filePicker.current
  else forms.file_access.path = filePicker.current
  closeFilePicker()
}

const chooseWriteTarget = () => {
  const problem = filenameProblem(filePicker.filename)
  if (problem) {
    filePicker.filenameError = problem
    return
  }
  forms.file_access.path = joinPickerPath(filePicker.current, filePicker.filename.trim())
  closeFilePicker()
}

// 类 shell 拆分：只用于把输入框文本转成参数数组，不会经过 shell 执行
const splitArgs = text => {
  const out = []
  const re = /"((?:\\.|[^"\\])*)"|'([^']*)'|(\S+)/g
  let m
  while ((m = re.exec(text))) out.push(m[1] !== undefined ? m[1].replace(/\\(.)/g, '$1') : m[2] ?? m[3])
  return out
}

const buildArgs = () => {
  const f = forms[name.value]
  switch (name.value) {
    case 'search_notes': return { query: f.query, top_k: Number(f.top_k) }
    case 'file_access': {
      const args = { action: f.action, path: f.path }
      if (f.action === 'write') {
        args.content = f.content
        args.mode = f.mode
      } else if (f.action === 'replace') {
        args.old_text = f.old_text
        args.new_text = f.new_text
      }
      if (['write', 'replace'].includes(f.action) && f.expected_sha256) {
        args.expected_sha256 = f.expected_sha256
      }
      return args
    }
    case 'git_query': return { operation: f.operation, repo_path: f.repo_path, limit: Number(f.limit) }
    case 'ci_status': return f.source === 'github' ? { source: f.source, repository: f.repository, limit: Number(f.limit) } : { source: f.source, limit: Number(f.limit) }
    case 'run_command': return { command: splitArgs(f.command), cwd: f.cwd, timeout_seconds: Number(f.timeout_seconds) || null }
  }
}

const preview = computed(() => ({ jsonrpc: '2.0', method: 'tools/call', params: { name: name.value, arguments: buildArgs() } }))

const select = t => { name.value = t; current.value = null; callError.value = '' }
const applyPreset = p => Object.assign(forms[name.value], p.form)

const call = async () => {
  loading.value = true
  callError.value = ''
  const args = buildArgs()
  try {
    const res = await callTool(name.value, args)
    const entry = { ...res, name: name.value, args, time: new Date().toLocaleTimeString() }
    current.value = entry
    history.unshift(entry)
    if (history.length > 30) history.pop()
  } catch (e) {
    callError.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const tools = await listTools()
    descriptions.value = Object.fromEntries(tools.map(t => [t.name, t.description]))
  } catch { /* 离线时使用本地说明 */ }
})
</script>

<style scoped>
.layout { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 24px; align-items: start; }
.tool-list { padding: 8px; display: flex; flex-direction: column; gap: 4px; position: sticky; top: 24px; }
.tool-btn { background: transparent; border: none; color: var(--text); justify-content: flex-start; min-height: 52px; padding: 8px 12px; gap: 12px; text-align: left; }
.tool-btn:hover { background: var(--soft); filter: none !important; }
.tool-btn.active { background: var(--blue-soft); }
.stackless { display: flex; flex-direction: column; line-height: 1.3; }
.form-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.form-grid .wide { grid-column: span 2; }
.form-grid .full { grid-column: 1 / -1; }
.path-picker-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.file-picker-backdrop { position: fixed; inset: 0; z-index: 100; background: rgba(22, 24, 29, .44); display: grid; place-items: center; padding: 24px; }
.file-picker-dialog { width: min(680px, 100%); max-height: calc(100vh - 48px); overflow: auto; background: var(--canvas); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 20px 60px rgba(0, 0, 0, .18); padding: 24px; outline: none; }
.file-picker-head { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
.file-picker-head > div { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.icon-button { min-height: 34px; width: 34px; padding: 0; justify-content: center; background: transparent; color: var(--text-2); border-color: transparent; font-size: 24px; }
.picker-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.picker-current { min-width: 0; overflow-wrap: anywhere; }
.picker-state { min-height: 180px; display: grid; place-items: center; border: 1px dashed var(--border); border-radius: var(--radius); }
.picker-list { list-style: none; margin: 0; padding: 4px; min-height: 180px; max-height: 340px; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius); }
.picker-list li + li { border-top: 1px solid var(--border); }
.picker-entry { width: 100%; min-height: 44px; display: grid; grid-template-columns: 24px minmax(0, 1fr) auto; gap: 8px; align-items: center; text-align: left; background: transparent; color: var(--text); border: 0; border-radius: 0; }
.picker-entry:hover:not(:disabled), .picker-entry:focus-visible { background: var(--soft); filter: none; }
.picker-entry:disabled { color: var(--text-2); background: transparent; }
.picker-entry-icon { color: var(--blue); text-align: center; }
.picker-entry-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-write { margin-top: 16px; display: flex; flex-direction: column; gap: 8px; }
.picker-write-hint { margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--soft); color: var(--text-2); }
.picker-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 20px; }
details summary { cursor: pointer; color: var(--blue); }
.clickable { cursor: pointer; }
.clickable:hover td { background: var(--soft); }
.args { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 960px) {
  .layout { grid-template-columns: 1fr; }
  .tool-list { position: static; flex-direction: row; overflow-x: auto; }
  .tool-btn { flex-shrink: 0; }
  .form-grid { grid-template-columns: 1fr; }
  .form-grid .wide, .form-grid .full { grid-column: auto; }
  .file-picker-backdrop { padding: 12px; }
  .file-picker-dialog { padding: 18px; max-height: calc(100vh - 24px); }
  .path-picker-row { grid-template-columns: 1fr; }
}
</style>
