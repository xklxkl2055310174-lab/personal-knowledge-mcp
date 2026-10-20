import { computed, reactive } from 'vue'

// The API key intentionally lives only in JavaScript memory. Reloading the page clears it.
export const providerState = reactive({
  id: 'local',
  label: '本地演示（无需 API）',
  model: '',
  base_url: '',
  api_key: '',
  api_version: '2024-10-21',
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
    api_version: '2024-10-21'
  })
}

export function providerPayload() {
  if (!providerState.configured || providerState.id === 'local') return null
  return {
    id: providerState.id,
    model: providerState.model,
    base_url: providerState.base_url,
    api_key: providerState.api_key,
    api_version: providerState.api_version
  }
}
