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
无 usage 时标记 estimate。无模型的 summary 是抽取式，不伪装成生成式摘要。

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

delegated 将检索工具权限限制给检索子 Agent，总结子 Agent 无工具。single 把真实 tools/list
转为模型函数定义，模型调用仍经 MCP Client。两者都有步骤和 token 上限，最终引用必须来自
本轮证据。无 API Key 时保留确定性流程用于测试，但不产生可用于简历的模型效果数字。

## 可观测性

SQLite Trace 采用 Task→Step→ToolCall→Attempt；工具参数脱敏、截断。JSONL
`agent_metrics` 为兼容现有前端保留。SQLite 适合单机项目；生产可迁移到 OpenTelemetry
与集中存储。

## 评测

规则评测检查期望要点和引用，不以“有检索结果”为成功。workspace 使用临时副本。
本地模式是 CI 回归；真实模型报告必须写明 provider/model/时间/任务数，不编造对比数字。
