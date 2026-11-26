// 访问后端 Web 辅助接口 /api/*（统一返回 { ok, data, error }）
async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options
    })
  } catch (err) {
    throw new Error(`无法连接后端：${err.message}`)
  }
  let body = null
  try { body = await res.json() } catch { /* ignore */ }
  if (!body) throw new Error(`HTTP ${res.status}`)
  if (!body.ok) throw new Error(body.error?.message || `HTTP ${res.status}`)
  return body.data
}

export const api = {
  overview: () => request('/overview'),
  ask: payload => request('/agent/ask', { method: 'POST', body: JSON.stringify(payload) }),
  compare: payload => request('/agent/compare', { method: 'POST', body: JSON.stringify(payload) }),
  conversation: id => request(`/conversations/${encodeURIComponent(id)}`),
  clearConversation: id => request(`/conversations/${encodeURIComponent(id)}`, {
    method: 'DELETE'
  }),
  metrics: () => request('/agent/metrics'),
  providers: () => request('/providers'),
  testProvider: provider => request('/provider/test', {
    method: 'POST',
    body: JSON.stringify({ provider })
  }),
  saveProvider: provider => request('/provider/save', {
    method: 'POST',
    body: JSON.stringify({ provider })
  }),
  deleteSavedProvider: () => request('/provider/saved', { method: 'DELETE' }),
  notes: () => request('/notes'),
  note: path => request(`/notes/content?path=${encodeURIComponent(path)}`)
}

export async function health() {
  const res = await fetch('/health')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
