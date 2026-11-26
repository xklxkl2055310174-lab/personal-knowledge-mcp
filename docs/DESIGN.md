# 设计取舍

## 协议边界

MCP Server 只负责工具发现、Schema 与调用适配；业务在 Service。仓库内 Agent 也使用
`mcp.Client`，不直接 import Service，因此与 Claude Code/Cursor 走同一协议边界。
工具失败同时保留 structuredContent，并返回 MCP `isError=true`。

## 统一契约

所有工具返回 `ToolResult`：成功放 data，失败放 error，meta 由注册层补
`trace_id/tool/duration_ms/attempts/caller`。参数约束在 Service 再校验，避免 SDK Schema
校验把业务错误绕过统一信封；operation/source 仍使用 Literal 生成清晰枚举。

## 检索

选择 SQLite 持久化增量索引 + 纯 Python 评分，而不是引入向量数据库。Markdown 按标题分块，
中文使用 unigram/bigram，解决整句被当成单词的问题。优点是零外部服务、可解释、可离线测试；
缺点是同义词召回弱，下一步可在固定评测上比较 BM25/Embedding/Rerank。

## 上下文

full、relevant、summary 使用不同输入：完整命中文档、高分 chunk、压缩 chunk。
裁剪优先级是低相关块→块正文→末尾字符，来源坐标优先保留。供应商 usage 优先于估算；
无 usage 时标记 estimate。无模型的 summary 是抽取式，不伪装成生成式摘要。这里的
`max_tokens` 只控制注入证据或单次工具结果，不是供应商累计 token、输出或费用上限。

会话记忆和工具证据分开管理。SQLite 最多保留每个会话 50 条消息；每轮从最新消息向前选择，
记忆注入不超过 `min(max_tokens, 1200)`。single 直接接收已裁剪历史；delegated 只有协调器接收
历史，子 Agent 只收到明确子任务。这样既能理解“它/刚才那个”等追问，又减少无关上下文和
权限扩散。记忆内容是用户问题与最终回答，不包含工具原始结果和系统提示词。

## Verification Loop

统一循环包含 execute、verify、classify、retry/fallback、record。指数退避带 jitter 和总
deadline，只重试 retryable timeout/transient。file_access 写前备份、失败回滚；Git
结构化解析；GitHub 网络/5xx/限流回退 local stale；run_command 非幂等，最大尝试为 1。

## 文件一致性

原子替换保证读者不会看到半文件，SHA 乐观锁检测跨进程并发。asyncio.Lock 只优化同进程竞争，
不能替代 SHA。备份位于忽略的 `.runtime/backups`，不是版本控制或长期备份系统。

## 受限执行与 Docker

本地模式只传最小环境、限制 cwd/参数/时长/输出，并终止进程树。它仍继承服务账号的操作系统
权限，且 file_access 可以先写脚本，因此只能称“受限执行”。Docker 模式关闭网络、非 root、
只挂 workspace、限制 CPU/内存/PID，并使用只读根文件系统；这才是项目中的隔离沙箱。

## Agent 模式

delegated 使用“协调器→专职子 Agent”两层结构。协调器只看到五个委派函数，不直接执行工具；
知识、文件、Git、CI、命令子 Agent 通过 RestrictedToolClient 分别只获得一个 MCP 工具，避免
把所有权限交给每个工作单元。子 Agent 内部仍使用模型 function calling + MCP Client，结果经
上下文预算裁剪后返回协调器。协调器可按依赖顺序多次委派，并校验知识引用。一个 Provider
实例可以承担不同角色，但每个角色有独立系统提示、消息上下文和工具白名单。

知识子 Agent 额外设置每任务 3 次 `search_notes` 实际调用上限，避免模型通过无限查询扩大
token 和延迟。每次返回先按 `path:line` 去重，再做上下文打包；Trace 保存查询参数、调用序号、
去重数量和实际注入的裁剪后文本。知识答案按段落/列表项计算 `citation_coverage`，未引用本轮
证据且未明确写“无证据”的结论会触发一次新的模型修订轮。该检查验证引用结构与来源集合，
不做自然语言蕴含判定。

single 把真实 tools/list 转为模型函数定义，支持工具调用的模型可在 5 个工具中直接选择，调用
仍经 MCP Client。两种模式都限制最多轮数、记录 token 与实际工具调用，但不设置应用层累计
token 上限。无 API Key 时 delegated 回退到固定 SearchAgent→ContextBudgetManager→SummaryAgent
链供离线测试；它不属于动态委派。single 没有模型时直接拒绝运行。

## CI 语义

ci_status 是只读状态查询，不负责触发 CI。本地模式读取 `.github/workflows/*.yml` 和可选的
`.ci/status.json`，缺少状态文件时 `state=unknown`；GitHub 模式读取 Actions runs。GitHub
瞬时失败可以回退本地并标记 stale，但本地 unknown 不能解释为 passed 或 failed。

## 可观测性

SQLite Trace 采用 Task→Step→ToolCall→Attempt；工具参数脱敏、截断。JSONL
`agent_metrics` 为兼容现有前端保留。SQLite 适合单机项目；生产可迁移到 OpenTelemetry
与集中存储。该审计只覆盖经过 MCP 注册层的调用，不是操作系统级全量行为审计。

## 评测

规则评测检查期望要点和引用，不以“有检索结果”为成功。workspace 使用临时副本。
本地模式是 CI 回归；真实模型报告必须写明 provider/model/时间/任务数，不编造对比数字。
配置支持工具调用的 Provider 后，评测器的 QA 路径按 mode 真实运行 ToolCallingAgent 或
DelegatedAgent。无 Provider 时仅 delegated 使用固定 KnowledgeAgent 做确定性回归，single
明确报错。修复前生成的历史报告不能比较编排效果，必须在相同实验条件下重新生成。
