<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <img src="/favicon.svg" alt="" width="28" height="28" />
        <div>
          <div class="brand-name">Knowledge MCP</div>
          <div class="brand-sub">Agent 工具化控制台</div>
        </div>
      </div>
      <nav>
        <router-link v-for="item in nav" :key="item.to" :to="item.to" class="nav-item">
          <span aria-hidden="true">{{ item.icon }}</span>{{ item.label }}
        </router-link>
      </nav>

      <div class="sidebar-foot">
        <button class="provider-entry" type="button" @click="openProviderDialog">
          <span class="provider-icon" aria-hidden="true">✦</span>
          <span class="provider-copy">
            <span class="provider-title">模型 API</span>
            <span class="provider-current">{{ providerLabel }}</span>
          </span>
          <span aria-hidden="true">›</span>
        </button>
        <div class="conn" :title="conn.detail">
          <span class="dot" :class="conn.ok ? 'green' : conn.ok === false ? 'red' : ''"></span>
          <span>{{ conn.ok ? 'MCP Server 在线' : conn.ok === false ? 'MCP Server 离线' : '连接中…' }}</span>
        </div>
      </div>
    </aside>
    <main class="main">
      <router-view />
    </main>
  </div>

  <Teleport to="body">
    <div v-if="providerDialog" class="modal-backdrop" @click.self="closeProviderDialog">
      <section class="provider-dialog" role="dialog" aria-modal="true" aria-labelledby="provider-title">
        <div class="dialog-head">
          <div>
            <h2 id="provider-title">接入模型 API</h2>
            <p class="muted small">配置真实 LLM 作为总结子 Agent。密钥只保存在本页内存，刷新即清除。</p>
          </div>
          <button class="icon-button" type="button" aria-label="关闭" @click="closeProviderDialog">×</button>
        </div>

        <div v-if="providerError" class="error-box">{{ providerError }}</div>

        <div class="dialog-body stack">
          <label class="field">
            模型供应商
            <select v-model="form.id" @change="applyPreset">
              <option v-for="item in providers" :key="item.id" :value="item.id">
                {{ item.label }}
              </option>
            </select>
          </label>

          <template v-if="form.id !== 'local'">
            <p v-if="selectedProvider?.hint" class="provider-hint small">
              {{ selectedProvider.hint }}
            </p>
            <label class="field">
              模型名称
              <input v-model.trim="form.model" placeholder="例如 gpt-5-mini / deepseek-chat" autocomplete="off" />
            </label>
            <label class="field">
              Base URL
              <input v-model.trim="form.base_url" class="mono" placeholder="https://.../v1" autocomplete="off" />
            </label>
            <label class="field">
              API Key
              <div class="secret-row">
                <input v-model.trim="form.api_key" :type="showKey ? 'text' : 'password'"
                       :placeholder="providerState.saved ? '已使用 Windows DPAPI 安全保存' : '输入一次后可安全保存到本机'"
                       autocomplete="off" />
                <button class="secondary show-key" type="button" @click="showKey = !showKey">
                  {{ showKey ? '隐藏' : '显示' }}
                </button>
              </div>
            </label>
            <p v-if="providerState.saved" class="muted small">
              API Key 已由当前 Windows 用户加密保存；留空即可继续使用。更改供应商或模型时请重新输入。
            </p>
            <label v-if="form.id === 'azure-openai'" class="field">
              API Version
              <input v-model.trim="form.api_version" class="mono" placeholder="2024-10-21" />
            </label>
          </template>
          <div v-else class="local-note">
            不调用外部模型，继续使用项目自带的确定性总结器。适合零成本学习 MCP 流程。
          </div>

          <div v-if="testResult" class="test-result" :class="testResult.ok ? 'success' : 'failure'">
            <strong>{{ testResult.ok ? '连接成功' : '连接失败' }}</strong>
            <span>{{ testResult.message }}</span>
          </div>
        </div>

        <div class="dialog-actions">
          <button v-if="providerState.saved" class="ghost" type="button" :disabled="saving" @click="forgetProvider">
            删除已保存配置
          </button>
          <button v-if="providerState.configured" class="ghost" type="button" @click="useLocal">
            切回本地模式
          </button>
          <span class="spacer"></span>
          <button class="secondary" type="button" @click="closeProviderDialog">取消</button>
          <button class="secondary" type="button" :disabled="testing" @click="testConnection">
            {{ testing ? '测试中…' : '测试连接' }}
          </button>
          <button type="button" :disabled="saving" @click="saveProvider">
            {{ saving ? '保存中…' : '安全保存到本机' }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api, health } from './api/http'
import { providerLabel, providerState, resetProvider, setProvider } from './provider-state'

const nav = [
  { to: '/', label: '总览', icon: '◎' },
  { to: '/agent', label: 'Agent 问答', icon: '✦' },
  { to: '/tools', label: '工具调试台', icon: '⚙' },
  { to: '/files', label: '笔记与工作区', icon: '▤' }
]

const conn = reactive({ ok: null, detail: '' })
const providers = ref([
  {
    id: 'local', label: '本地演示（无需 API）', default_model: '', base_url: '',
    requires_key: false, hint: ''
  }
])
const providerDialog = ref(false)
const providerError = ref('')
const testing = ref(false)
const saving = ref(false)
const showKey = ref(false)
const testResult = ref(null)
const form = reactive({
  id: 'local', model: '', base_url: '', api_key: '', api_version: '2024-10-21'
})

const selectedProvider = computed(() => providers.value.find(item => item.id === form.id))

let timer
const ping = async () => {
  try {
    const h = await health()
    conn.ok = h.status === 'ok'
    conn.detail = `${h.service} v${h.version}`
  } catch (e) {
    conn.ok = false
    conn.detail = e.message
  }
}

const loadProviders = async () => {
  if (providers.value.length > 1) return
  try {
    const data = await api.providers()
    providers.value = data.providers
    if (data.saved_provider && !providerState.configured) {
      setProvider({ ...data.saved_provider, api_key: '' })
    }
  } catch (e) {
    providerError.value = `无法读取供应商列表：${e.message}`
  }
}

const openProviderDialog = async () => {
  Object.assign(form, {
    id: providerState.id,
    model: providerState.model,
    base_url: providerState.base_url,
    api_key: providerState.api_key,
    api_version: providerState.api_version
  })
  providerError.value = ''
  testResult.value = null
  showKey.value = false
  providerDialog.value = true
  await loadProviders()
}

const closeProviderDialog = () => { providerDialog.value = false }

const applyPreset = () => {
  const item = selectedProvider.value
  if (!item) return
  form.model = item.default_model || ''
  form.base_url = item.base_url || ''
  form.api_key = ''
  testResult.value = null
  providerError.value = ''
}

const savedFormMatches = () => (
  providerState.saved &&
  form.id === providerState.id &&
  form.model === providerState.model &&
  form.base_url === providerState.base_url &&
  form.api_version === providerState.api_version
)

const validateProvider = (allowSaved = false) => {
  const item = selectedProvider.value
  if (!item) throw new Error('请选择模型供应商')
  if (form.id === 'local') return item
  if (!form.model) throw new Error('请填写模型名称')
  if (!form.base_url) throw new Error('请填写 Base URL')
  if (item.requires_key && !form.api_key && !(allowSaved && savedFormMatches())) {
    throw new Error('请填写 API Key')
  }
  return item
}

const payload = () => ({
  id: form.id,
  model: form.model,
  base_url: form.base_url,
  api_key: form.api_key,
  api_version: form.api_version
})

const testConnection = async () => {
  providerError.value = ''
  testResult.value = null
  try {
    validateProvider(true)
    testing.value = true
    const result = await api.testProvider(
      savedFormMatches() && !form.api_key ? { use_saved: true } : payload()
    )
    const latency = result.latency_ms != null ? ` · ${result.latency_ms} ms` : ''
    testResult.value = { ok: true, message: `${result.preview}${latency}` }
  } catch (e) {
    testResult.value = { ok: false, message: e.message }
  } finally {
    testing.value = false
  }
}

const saveProvider = async () => {
  providerError.value = ''
  try {
    const item = validateProvider(true)
    if (form.id === 'local') {
      resetProvider()
    } else if (savedFormMatches() && !form.api_key) {
      closeProviderDialog()
      return
    } else {
      saving.value = true
      const result = await api.saveProvider(payload())
      setProvider({ ...result.provider, api_key: '', label: item.label })
    }
    closeProviderDialog()
  } catch (e) {
    providerError.value = e.message
  } finally {
    saving.value = false
  }
}

const forgetProvider = async () => {
  providerError.value = ''
  try {
    saving.value = true
    await api.deleteSavedProvider()
    resetProvider()
    closeProviderDialog()
  } catch (e) {
    providerError.value = e.message
  } finally {
    saving.value = false
  }
}

const useLocal = () => {
  resetProvider()
  closeProviderDialog()
}

onMounted(() => {
  ping()
  loadProviders()
  timer = setInterval(ping, 15000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.sidebar {
  width: 232px; flex-shrink: 0; background: var(--canvas); border-right: 1px solid var(--border);
  padding: 24px 16px; display: flex; flex-direction: column; gap: 24px; position: sticky; top: 0; height: 100vh;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 0 8px; }
.brand-name { font-weight: 650; }
.brand-sub { font-size: 13px; color: var(--text-2); }
nav { display: flex; flex-direction: column; gap: 4px; }
.nav-item {
  display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 0 12px;
  border-radius: var(--radius); color: var(--text); font-size: 15px;
}
.nav-item span { width: 18px; text-align: center; color: var(--text-2); }
.nav-item:hover { background: var(--soft); text-decoration: none; }
.nav-item.router-link-exact-active { background: var(--blue-soft); color: #1b67b3; font-weight: 500; }
.nav-item.router-link-exact-active span { color: var(--blue); }
.sidebar-foot { margin-top: auto; display: flex; flex-direction: column; gap: 12px; }
.provider-entry {
  width: 100%; min-height: 58px; padding: 9px 10px; background: var(--soft); color: var(--text);
  border-color: var(--border); display: grid; grid-template-columns: 26px minmax(0, 1fr) auto;
  text-align: left; gap: 8px;
}
.provider-entry:hover { border-color: var(--blue); filter: none !important; }
.provider-icon { color: var(--blue); font-size: 18px; }
.provider-copy { display: flex; flex-direction: column; min-width: 0; }
.provider-title { font-size: 14px; font-weight: 600; }
.provider-current { color: var(--text-2); font-size: 12px; overflow: hidden; text-overflow: ellipsis; }
.conn { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-2); padding: 0 8px; }
.main { flex: 1; min-width: 0; padding: 40px 48px 64px; max-width: 1200px; }

.modal-backdrop {
  position: fixed; inset: 0; z-index: 100; background: rgba(22, 24, 29, .44);
  display: grid; place-items: center; padding: 24px;
}
.provider-dialog {
  width: min(600px, 100%); max-height: calc(100vh - 48px); overflow: auto;
  background: var(--canvas); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, .18); padding: 24px;
}
.dialog-head { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 20px; }
.dialog-head > div { display: flex; flex-direction: column; gap: 6px; flex: 1; }
.icon-button {
  min-height: 34px; width: 34px; padding: 0; justify-content: center; background: transparent;
  color: var(--text-2); border-color: transparent; font-size: 24px;
}
.dialog-body { margin-top: 16px; }
.provider-hint, .local-note, .test-result { padding: 10px 12px; border-radius: var(--radius); }
.provider-hint, .local-note { background: var(--soft); color: var(--text-2); }
.secret-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.show-key { padding: 0 12px; }
.test-result { display: flex; flex-direction: column; gap: 2px; font-size: 14px; }
.test-result.success { background: var(--green-soft); color: #2f7a52; }
.test-result.failure { background: var(--red-soft); color: var(--red); }
.dialog-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 24px; }

@media (max-width: 860px) {
  .shell { flex-direction: column; }
  .sidebar { width: 100%; height: auto; position: static; flex-direction: row; align-items: center; flex-wrap: wrap; padding: 12px 16px; gap: 12px; border-right: none; border-bottom: 1px solid var(--border); }
  nav { flex-direction: row; flex-wrap: wrap; }
  .nav-item { min-height: 40px; padding: 0 10px; font-size: 14px; }
  .brand-sub { display: none; }
  .sidebar-foot { margin-top: 0; margin-left: auto; flex-direction: row; align-items: center; }
  .provider-entry { width: 190px; min-height: 44px; }
  .conn { display: none; }
  .main { padding: 24px 16px 48px; }
}

@media (max-width: 560px) {
  .sidebar-foot { width: 100%; }
  .provider-entry { width: 100%; }
  .modal-backdrop { padding: 12px; }
  .provider-dialog { padding: 18px; max-height: calc(100vh - 24px); }
  .dialog-actions .spacer { display: none; }
  .dialog-actions button { flex: 1; justify-content: center; }
}
</style>
