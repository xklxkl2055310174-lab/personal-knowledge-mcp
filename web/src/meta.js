// 前端展示用的静态说明（与后端 README 中的工具表保持一致）
export const TOOL_META = {
  search_notes: { label: '笔记检索', description: '从本地 Markdown 笔记中检索相关证据，并返回真实来源位置。', guard: '文件大小、后缀、结果数量限制', icon: '🔎' },
  file_access: { label: '文件读写', description: '在安全工作区内列出目录、读取文件、写入文件或局部替换。', guard: '路径沙箱、SHA-256 冲突检测、原子写入与写后校验', icon: '📄' },
  git_query: { label: 'Git 查询', description: '只读查询当前仓库的状态、分支、提交记录和代码差异。', guard: '固定只读子命令、仓库根目录限制', icon: '🌿' },
  ci_status: { label: '持续集成状态', description: '读取本地持续集成记录或 GitHub Actions 运行状态。', guard: '参数校验、超时、访问令牌可选', icon: '🧪' },
  run_command: { label: '受限命令执行', description: '在安全工作区内执行白名单允许的命令，并限制时间和输出。', guard: '命令白名单、禁止系统命令解释器、工作目录沙箱、超时、输出截断', icon: '⌨️' }
}

export const ERROR_CATEGORY = {
  parameter: { label: '参数错误', tone: 'orange', retry: false, hint: '相同输入不会自行变正确，应修正参数而不是重试' },
  permission: { label: '权限不足', tone: 'red', retry: false, hint: '越过沙箱或白名单，重试无意义，应直接拒绝' },
  conflict: { label: '写入冲突', tone: 'red', retry: false, hint: '文件已变化，应重新读取最新内容和 SHA-256 后再决定是否写入' },
  timeout: { label: '超时', tone: 'orange', retry: true, hint: '可在上限内带退避重试' },
  transient: { label: '瞬时错误', tone: 'orange', retry: true, hint: '网络 / IO 抖动，可有限重试' },
  internal: { label: '内部错误', tone: 'red', retry: false, hint: '服务端缺陷，应记录并告警' }
}

export const STRATEGIES = [
  { value: 'full', label: '完整内容', desc: '完整注入全部检索结果' },
  { value: 'relevant', label: '相关内容', desc: '按相关度排序，用满预算为止' },
  { value: 'summary', label: '压缩摘要', desc: '压缩为要点列表回填' }
]
