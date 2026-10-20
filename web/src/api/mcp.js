// 浏览器端最小 MCP Client：通过 Streamable HTTP 直接以 JSON-RPC 2.0 调用 /mcp
// 服务端以 stateless + json_response 模式运行，因此每个请求都是独立的 POST。

const ENDPOINT = '/mcp'
const CLIENT_INFO = { name: 'knowledge-mcp-web', version: '0.1.0' }
let seq = 0
let protocolVersion = null

export class McpError extends Error {
  constructor(message, trace) {
    super(message)
    this.trace = trace
  }
}

function parsePayload(text, contentType) {
  if (!text) return null
  if (contentType && contentType.includes('text/event-stream')) {
    const data = text.split('\n').filter(line => line.startsWith('data:'))
    return data.length ? JSON.parse(data[data.length - 1].slice(5)) : null
  }
  return JSON.parse(text)
}

async function rpc(method, params) {
  const request = { jsonrpc: '2.0', id: ++seq, method, ...(params ? { params } : {}) }
  const headers = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' }
  if (protocolVersion) headers['MCP-Protocol-Version'] = protocolVersion

  const started = performance.now()
  let res
  try {
    res = await fetch(ENDPOINT, { method: 'POST', headers, body: JSON.stringify(request) })
  } catch (err) {
    throw new McpError(`无法连接 MCP Server：${err.message}`, { request })
  }
  const text = await res.text()
  const duration = Math.round(performance.now() - started)
  let response
  try {
    response = parsePayload(text, res.headers.get('content-type'))
  } catch {
    throw new McpError(`HTTP ${res.status}：响应不是合法 JSON`, { request, raw: text, duration })
  }
  const trace = { request, response, duration, status: res.status }
  if (!res.ok && !response) throw new McpError(`HTTP ${res.status}`, trace)
  if (response?.error) throw new McpError(`JSON-RPC ${response.error.code}：${response.error.message}`, trace)
  return { result: response.result, trace }
}

export async function initialize() {
  const { result, trace } = await rpc('initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: CLIENT_INFO
  })
  protocolVersion = result.protocolVersion
  return { ...result, trace }
}

export async function listTools() {
  const { result } = await rpc('tools/list')
  return result.tools
}

/** 返回 { structured: ToolResult, isError, trace } */
export async function callTool(name, args) {
  const { result, trace } = await rpc('tools/call', { name, arguments: args })
  let structured = result.structuredContent
  if (!structured) {
    try { structured = JSON.parse(result.content?.[0]?.text ?? 'null') } catch { structured = null }
  }
  // 兼容某些 SDK 把非 dict 返回值包在 { result: ... } 里
  if (structured && structured.result && structured.ok === undefined) structured = structured.result
  return { structured, isError: !!result.isError, trace }
}
