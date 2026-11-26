import { computed, reactive } from 'vue'

// Persisted credentials stay encrypted on the backend; the browser only receives safe metadata.
export const providerState = reactive({
  id: 'local',
  label: '本地演示（无需 API）',
  model: '',
  base_url: '',
  api_key: '',
  api_version: '2024-10-21',
  supports_tools: false,
  saved: false,
  configured: false
})

export const providerLabel = computed(() => {
  if (providerState.id === 'local') return '本地演示 Agent'
  const model = providerState.model ? ` · ${providerState.model}` : ''
  return `${providerState.label}${model}`
})

export function setProvider(config) {
  Object.assign(providerState, config, { configured: config.id !== 'local' })
}

export function resetProvider() {
  setProvider({
    id: 'local',
    label: '本地演示（无需 API）',
    model: '',
    base_url: '',
    api_key: '',
    api_version: '2024-10-21',
    supports_tools: false,
    saved: false
  })
}

export function providerPayload() {
  if (!providerState.configured || providerState.id === 'local') return null
  if (providerState.saved) return { use_saved: true }
  return {
    id: providerState.id,
    model: providerState.model,
    base_url: providerState.base_url,
    api_key: providerState.api_key,
    api_version: providerState.api_version
  }
}
