# 会话记忆功能报告

更新时间：2026-10-29

## 解决的问题

修改前每次 `/api/agent/ask` 都从空消息列表开始，模型只看到系统提示和当前问题。因此“它有
哪些工具”“继续解释刚才第二点”这类追问缺少指代上下文。

修改后每个 Web 对话拥有一个 `conversation_id`。后端在本地 SQLite 中保存用户问题和最终
回答，下一轮先读取并裁剪历史，再注入 Agent 消息列表。

```text
浏览器 conversation_id
  -> SQLite 读取最近消息
  -> 记忆 Token 裁剪（优先保留最新消息）
  -> system + history + current user
  -> single Agent 或 delegated 协调 Agent
  -> 最终回答写回 SQLite
```

## 记忆范围

- 每个会话最多保存 50 条消息。
- 每轮最多注入 `min(max_tokens, 1200)` 个估算 tokens。
- 只保存 `user` 与 `assistant` 最终文本。
- 不保存 MCP 工具原始结果、系统提示词或 API Key。
- 记忆数据库为 `.runtime/traces.sqlite3`，服务重启后仍保留，Git 不提交。

## 不同 Agent 模式的使用方式

- single：历史直接进入单 Agent 的模型消息列表。
- delegated：只有协调 Agent 看到历史；专职子 Agent 只收到协调器整理后的具体任务。
- 无 Provider 离线回退：历史参与 `search_notes` 检索查询，帮助补足追问中的指代。

## 前端功能

- 显示当前会话最近 8 条消息。
- 显示本轮注入消息数和估算 Token。
- “新对话”生成新的 conversation_id，不带入旧上下文。
- “清空记忆”删除当前 conversation_id 的所有消息。

## 安全与限制

该功能是本机单用户会话记忆，不是生产级长期记忆。SQLite 中的对话文本没有额外加密，也没有
登录、租户隔离、语义检索、自动摘要或跨对话用户画像，因此不应存放密码、API Key 等敏感信息。

