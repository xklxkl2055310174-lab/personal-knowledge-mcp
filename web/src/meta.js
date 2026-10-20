// 前端展示用的静态说明（与后端 README 中的工具表保持一致）
export const TOOL_META = {
  search_notes: { label: '笔记检索', guard: '文件大小、后缀、结果数量限制', icon: '🔎' },
  file_access: { label: '文件读写', guard: '相对路径沙箱、UTF-8、原子写入 + 写后校验', icon: '📄' },
  git_query: { label: 'Git 查询', guard: '固定只读子命令、仓库根目录限制', icon: '🌿' },
  ci_status: { label: 'CI 状态', guard: '参数校验、超时、Token 可选', icon: '🧪' },
  run_command: { label: '命令沙箱', guard: '白名单、shell=False、工作目录沙箱、超时、输出截断', icon: '⌨️' }
}

export const ERROR_CATEGORY = {
  parameter: { label: '参数错误', tone: 'orange', retry: false, hint: '相同输入不会自行变正确，应修正参数而不是重试' },
  permission: { label: '权限不足', tone: 'red', retry: false, hint: '越过沙箱或白名单，重试无意义，应直接拒绝' },
  timeout: { label: '超时', tone: 'orange', retry: true, hint: '可在上限内带退避重试' },
  transient: { label: '瞬时错误', tone: 'orange', retry: true, hint: '网络 / IO 抖动，可有限重试' },
  internal: { label: '内部错误', tone: 'red', retry: false, hint: '服务端缺陷，应记录并告警' }
}

export const STRATEGIES = [
  { value: 'full', label: 'full', desc: '完整注入全部检索结果' },
  { value: 'relevant', label: 'relevant', desc: '按相关度排序，用满预算为止' },
  { value: 'summary', label: 'summary', desc: '压缩为要点列表回填' }
]
