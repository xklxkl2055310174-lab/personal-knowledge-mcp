# HTTP API

服务默认 `127.0.0.1:8000`。MCP 端点是 `POST /mcp`；以下 `/api/*` 是 Web/任务管理
辅助接口，不替代 MCP。统一响应：

```json
{"ok": true, "data": {}, "error": null}
```

失败：`{"ok":false,"data":null,"error":{"code":"...","message":"..."}}`。

## 兼容接口

- `GET /api/overview`：服务配置、笔记数、旧指标汇总。
- `POST /api/agent/ask`：`query, strategy, max_tokens, provider?, mode?`。其中
  `max_tokens` 是注入证据/工具结果的上下文预算，不是模型累计 token 或费用上限。
- `POST /api/agent/compare`：同一 query 跑三种策略。
- `GET /api/agent/metrics`：保持原 JSONL 汇总格式。
- `GET /api/conversations/{id}`：读取当前会话最近 50 条用户/Assistant 消息。
- `DELETE /api/conversations/{id}`：清空该会话消息但保留会话 ID。
- `GET /api/providers`：供应商列表，新增 `supports_tools`，永不返回 Key。
- `POST /api/provider/test`：单次内存 Provider 连接测试。
- `POST /api/provider/save`：在 Windows 上用当前用户 DPAPI 加密 Key，保存到
  `.private/provider-config.json`；非 Windows 返回不支持。
- `DELETE /api/provider/saved`：删除已保存的本机 Provider 配置。
- `GET /api/notes`、`GET /api/notes/content?path=...`：知识库只读浏览。

请求中省略 `provider` 时优先使用环境变量配置，其次使用已保存配置；传入
`{"provider":{"use_saved":true}}` 可明确使用本机已保存配置。接口响应只返回供应商、模型、
Base URL 等公开字段，不返回明文或密文 Key。

## 任务

### POST /api/tasks

请求：

```json
{"query":"为什么要做失败分类","mode":"delegated","strategy":"relevant","max_tokens":800,"conversation_id":"demo_conversation_01"}
```

返回 `task_id,status=queued`。mode 为 `single|delegated`，strategy 为
`full|relevant|summary`，max_tokens 为 64-8000。连接支持工具调用的 Provider 后，delegated
由协调器委派五类最小权限子 Agent，single 由一个模型直接选择五个 MCP 工具。无 Provider 时
delegated 回退离线知识问答链，single 返回配置错误。两种真实模型模式都有有限交互轮数。
`conversation_id` 可省略，后端会生成并随结果返回；Web 会把它保存在浏览器 localStorage。
响应中的 `memory` 给出本轮注入消息数、估算 token、预算和是否截断。

### GET /api/tasks

`limit` 默认 20、最大实际返回 100；`offset` 默认 0。返回历史 items。

### GET /api/tasks/{id}

返回 Task 及 steps/tool_calls。不存在为 `task_not_found`。

### POST /api/tasks/{id}/cancel

设置 `cancel_requested` 并取消仍在运行的后台协程。返回状态 `cancelling`。

### GET /api/tasks/{id}/events

SSE，事件对象字段为 `event,timestamp,data`。事件类型：
`step_started,tool_call,tool_result,retry,fallback,llm_usage,step_finished,task_finished,error`。
客户端断开只停止流，不默认删除 Trace。

## 工具审计与指标

- `GET /api/tool-calls?tool=&category=&caller=&since=&limit=100&offset=0`
- `GET /api/metrics/failures?hours=720`：工具×错误分类。
- `GET /api/metrics/strategies`：兼容策略指标。
- `GET /api/metrics/delegation`：按 single/delegated 聚合任务。

## 笔记索引

- `GET /api/notes/index`：`files,chunks,last_indexed_at`。
- `POST /api/notes/reindex`：增量同步，返回 changed/removed/skipped 与总量。

## 评测报告

- `GET /api/eval/reports`：报告列表。
- `GET /api/eval/reports/{id}`：报告 JSON；ID 只允许字母数字、点、下划线、连字符。

## 数据模型

- Task：`id,query,mode,strategy,status,answer,metrics_json,error_json,cancel_requested,conversation_id`
- ConversationMessage：`id,conversation_id,role,content,created_at,metadata`
- Step：`task_id,name,status,started_at,finished_at,detail_json`
- ToolCall：`tool,arguments_json,caller,ok,error_category,error_code,attempts,duration_ms,trace_id`
- Attempt：`tool_call_id,number,duration_ms,outcome,category,code`
- Metrics：成功率、要点命中率、引用准确率、token、P50/P95、调用与重试
- EvalReport：`generated_at,mode,strategy,model,task_count,metrics,rows`

## 错误码

| HTTP/API code | 含义 |
| --- | --- |
| empty_query / query_too_long / invalid_budget | 请求参数错误 |
| invalid_mode / invalid_strategy | 枚举错误 |
| invalid_conversation_id | 会话 ID 格式或长度错误 |
| task_not_found / report_not_found | 资源不存在 |
| invalid_pagination / invalid_hours / invalid_report_id | 查询参数错误 |
| provider_failed / provider_test_failed | 模型服务失败（不会含 Key） |
| invalid_provider / provider_save_failed / provider_delete_failed | Provider 参数或本机持久化失败 |
| agent_failed / task_failed | Agent 执行失败；结合 trace 排查 |

MCP 工具错误码见 [TOOLS.md](TOOLS.md)。真实响应样例位于 `docs/fixtures`。
