<template>
  <div class="page">
    <header class="page-head">
      <h1>笔记与工作区</h1>
      <p>知识库笔记只读展示（Agent 的检索来源）；工作区通过 MCP <code>file_access</code> 工具在沙箱内浏览与编辑。</p>
    </header>

    <div class="segmented" role="tablist">
      <button role="tab" :aria-selected="tab === 'notes'" :class="{ active: tab === 'notes' }" @click="tab = 'notes'">知识库笔记</button>
      <button role="tab" :aria-selected="tab === 'workspace'" :class="{ active: tab === 'workspace' }" @click="openWorkspace">工作区（file_access）</button>
    </div>

    <!-- 知识库 -->
    <section v-if="tab === 'notes'" class="split">
      <div class="card list-card">
        <div class="card-head"><h2>笔记</h2><span class="badge">{{ notes.length }}</span></div>
        <button v-for="n in notes" :key="n.path" class="item" :class="{ active: note?.path === n.path }" @click="openNote(n.path)">
          <span class="mono">{{ n.path }}</span>
          <span class="muted small">{{ (n.size / 1024).toFixed(1) }} KB</span>
        </button>
        <div v-if="!notes.length" class="empty">{{ notesError || 'knowledge/notes 下还没有笔记' }}</div>
      </div>
      <div class="card viewer">
        <template v-if="note">
          <div class="card-head">
            <h2 class="mono">{{ note.path }}</h2>
            <router-link :to="{ path: '/tools', query: { name: 'search_notes' } }" class="small">在调试台检索 →</router-link>
          </div>
          <div class="code-lines" role="region" aria-label="笔记内容">
            <div v-for="(line, i) in noteLines" :key="i" :id="`L${i + 1}`" class="line" :class="{ hit: highlight === i + 1 }">
              <span class="ln">{{ i + 1 }}</span><span class="txt">{{ line || ' ' }}</span>
            </div>
          </div>
        </template>
        <div v-else class="empty">选择左侧笔记查看内容</div>
      </div>
    </section>

    <!-- 工作区 -->
    <section v-else class="split">
      <div class="card list-card">
        <div class="card-head">
          <h2>工作区</h2>
          <button class="ghost" @click="listDir(dir)">刷新</button>
        </div>
        <div class="crumbs mono small">
          <a href="#" @click.prevent="listDir('.')">workspace</a>
          <template v-for="(c, i) in crumbs" :key="i">
            / <a href="#" @click.prevent="listDir(crumbs.slice(0, i + 1).join('/'))">{{ c }}</a>
          </template>
        </div>
        <button v-if="dir !== '.'" class="item" @click="listDir(parentDir)"><span>↩ ..</span></button>
        <button v-for="e in entries" :key="e.name" class="item" :class="{ active: file?.path === join(e.name) }" @click="e.type === 'directory' ? listDir(join(e.name)) : openFile(join(e.name))">
          <span>{{ e.type === 'directory' ? '📁' : '📄' }} <span class="mono">{{ e.name }}</span></span>
          <span class="muted small">{{ e.size != null ? `${e.size} B` : '' }}</span>
        </button>
        <div v-if="!entries.length" class="empty">{{ wsError || '空目录' }}</div>
        <div class="row new-file">
          <input v-model="newName" placeholder="新文件名，如 notes.md" @keyup.enter="createFile" />
          <button class="secondary" :disabled="!newName.trim()" @click="createFile">新建</button>
        </div>
      </div>
      <div class="card viewer stack">
        <template v-if="file">
          <div class="row">
            <h2 class="mono">{{ file.path }}</h2>
            <span v-if="dirty" class="badge orange">未保存</span>
            <span class="spacer"></span>
            <button :disabled="saving" @click="saveFile">{{ saving ? '保存中…' : '保存（原子写入）' }}</button>
          </div>
          <textarea v-model="file.content" class="code" rows="16" @input="dirty = true"></textarea>
          <ToolResultView v-if="saveResult" :result="saveResult.structured" :duration="saveResult.trace.duration" />
        </template>
        <div v-else class="empty">选择或新建一个文件。所有路径都在工作区沙箱内解析，越界会被拒绝。</div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api/http'
import { callTool } from '../api/mcp'
import ToolResultView from '../components/ToolResultView.vue'

const route = useRoute()
const tab = ref('notes')

// ---- 知识库 ----
const notes = ref([])
const notesError = ref('')
const note = ref(null)
const highlight = ref(0)
const noteLines = computed(() => (note.value?.content || '').split('\n'))

const openNote = async (path, line = 0) => {
  try {
    note.value = await api.note(path)
    highlight.value = line
    if (line) {
      await nextTick()
      document.getElementById(`L${line}`)?.scrollIntoView({ block: 'center' })
    }
  } catch (e) { notesError.value = e.message }
}

// ---- 工作区（全部通过 MCP file_access） ----
const dir = ref('.')
const entries = ref([])
const wsError = ref('')
const file = ref(null)
const dirty = ref(false)
const saving = ref(false)
const saveResult = ref(null)
const newName = ref('')

const crumbs = computed(() => (dir.value === '.' ? [] : dir.value.split('/')))
const parentDir = computed(() => crumbs.value.slice(0, -1).join('/') || '.')
const join = name => (dir.value === '.' ? name : `${dir.value}/${name}`)

const listDir = async path => {
  wsError.value = ''
  try {
    const { structured } = await callTool('file_access', { action: 'list', path })
    if (!structured?.ok) throw new Error(structured?.error?.message || '列目录失败')
    dir.value = path
    entries.value = structured.data.entries
  } catch (e) { wsError.value = e.message; entries.value = [] }
}

const openFile = async path => {
  saveResult.value = null
  const { structured } = await callTool('file_access', { action: 'read', path })
  if (!structured?.ok) { wsError.value = structured?.error?.message; return }
  file.value = {
    path,
    content: structured.data.content,
    sha256: structured.data.sha256,
    isNew: false
  }
  dirty.value = false
}

const saveFile = async () => {
  saving.value = true
  try {
    const arguments_ = {
      action: 'write',
      path: file.value.path,
      content: file.value.content,
      mode: file.value.isNew ? 'create' : 'overwrite'
    }
    if (!file.value.isNew && file.value.sha256) arguments_.expected_sha256 = file.value.sha256
    saveResult.value = await callTool('file_access', arguments_)
    if (saveResult.value.structured?.ok) {
      file.value.sha256 = saveResult.value.structured.data.sha256
      file.value.isNew = false
      dirty.value = false
      listDir(dir.value)
    }
  } finally { saving.value = false }
}

const createFile = () => {
  const name = newName.value.trim()
  if (!name) return
  file.value = { path: join(name), content: '', sha256: null, isNew: true }
  dirty.value = true
  saveResult.value = null
  newName.value = ''
}

const openWorkspace = () => { tab.value = 'workspace'; if (!entries.value.length) listDir(dir.value) }

onMounted(async () => {
  try { notes.value = (await api.notes()).notes } catch (e) { notesError.value = e.message }
  if (route.query.note) openNote(String(route.query.note), Number(route.query.line) || 0)
  else if (notes.value.length) openNote(notes.value[0].path)
})
</script>

<style scoped>
.split { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 24px; align-items: start; }
.list-card { padding: 16px; display: flex; flex-direction: column; gap: 4px; }
.list-card .card-head { margin-bottom: 8px; padding: 0 4px; }
.item { background: transparent; border: none; color: var(--text); justify-content: space-between; min-height: 44px; padding: 0 12px; text-align: left; width: 100%; }
.item:hover { background: var(--soft); filter: none !important; }
.item.active { background: var(--blue-soft); }
.item .mono { font-size: 14px; overflow: hidden; text-overflow: ellipsis; }
.crumbs { padding: 0 4px 8px; color: var(--text-2); word-break: break-all; }
.new-file { margin-top: 12px; flex-wrap: nowrap; }
.viewer { min-height: 360px; }
.code-lines { font-family: var(--mono); font-size: 14px; line-height: 1.7; border: 1px solid var(--border); border-radius: var(--radius); overflow: auto; max-height: 640px; background: var(--soft); }
.line { display: flex; }
.line.hit { background: #fff4d6; }
.ln { width: 48px; flex-shrink: 0; text-align: right; padding-right: 12px; color: #a9a59e; user-select: none; border-right: 1px solid var(--border); }
.txt { padding: 0 16px; white-space: pre-wrap; word-break: break-word; }
@media (max-width: 860px) { .split { grid-template-columns: 1fr; } }
</style>
