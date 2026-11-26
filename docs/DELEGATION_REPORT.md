# 委派模式功能报告

更新时间：2026-10-29

## 修复前的问题

原 `delegated` 实际是固定知识问答链：`SearchAgent` 总是调用 `search_notes`，随后进行上下文
裁剪、总结和文件回写。它能演示职责拆分，但不能根据用户任务选择文件、Git、CI 或命令能力。
另外，评测器的 `--mode single` 只写入报告标签，QA 实际仍执行同一条 `KnowledgeAgent` 链。

## 修复后的执行链

连接支持 function calling 的 Provider 后，`delegated` 执行真实的两层委派：

```text
用户任务
  -> 协调 Agent：分析任务、选择一个或多个专职子 Agent
     -> 知识子 Agent -> MCP search_notes
     -> 文件子 Agent -> MCP file_access
     -> Git 子 Agent  -> MCP git_query
     -> CI 子 Agent   -> MCP ci_status
     -> 命令子 Agent -> MCP run_command
  -> 协调 Agent：合并结果、校验证据引用、生成最终回答
```

协调 Agent 只能看到五个 `delegate_*` 函数，不能直接调用 MCP 工具。每个子 Agent 通过
`RestrictedToolClient` 只能发现并调用自己的工具，实际执行仍跨越 MCP Client/Server 边界。

| 子 Agent | 唯一可见 MCP 工具 | 主要用途 |
| --- | --- | --- |
| `delegate_knowledge` | `search_notes` | 检索本地知识并保留 `path:line` 证据 |
| `delegate_files` | `file_access` | 工作区内 list/read/write/replace |
| `delegate_git` | `git_query` | 只读 status/branch/log/diff |
| `delegate_ci` | `ci_status` | 读取本地或 GitHub Actions 状态 |
| `delegate_command` | `run_command` | 白名单、cwd 和超时约束下执行命令 |

复杂任务可以顺序调用多个子 Agent。例如“分析 CI 状态并结合最近代码改动给出建议”可以先委派
CI 子 Agent，再委派 Git 子 Agent，最后由协调器汇总。所有底层工具调用仍写入 SQLite 审计。

## 安全与正确性

- 子 Agent 的权限由代码白名单限制，不依赖 Prompt 自觉。
- 文件、Git、CI 和命令仍使用原有 root、路径校验、命令白名单和 Verification Loop。
- 子 Agent 工具结果会经过上下文预算管理，再返回协调器，避免无上限注入。
- 子 Agent 失败被隔离并结构化返回协调器，不会自动扩大权限。
- 知识子 Agent 每任务最多实际调用 `search_notes` 3 次，超限请求被代码拦截。
- 多次查询命中的证据按 `path:line` 去重后再注入，避免同一证据重复消耗上下文。
- 调用 `search_notes` 后，子 Agent 和协调器两层都执行逐结论引用覆盖校验；每条结论必须引用
  本轮证据或明确声明“无证据”。这不等同于语义蕴含验证。
- 协调器和子 Agent 都有最大交互轮数，避免无限循环；仍不设置应用层累计 token 上限。

## 三种实际运行情况

| 情况 | 实际执行器 | 说明 |
| --- | --- | --- |
| `mode=single` + Provider | `ToolCallingAgent` | 一个 Agent 直接看到五个 MCP 工具 |
| `mode=delegated` + Provider | `DelegatedAgent` | 协调器选择最小权限专职子 Agent |
| `mode=delegated`、无 Provider | `KnowledgeAgent` | 离线固定知识问答回退，不冒充动态委派 |

`mode=single` 在无 Provider 时会明确拒绝运行。Web 页面会显示当前 delegated 是真实委派还是
离线回退。

## Trace 与评测变化

真实委派的 Web Trace 会展开为可见步骤：`intake`、一个或多个 `delegation_N`、
`coordination`。用户不需要进入一整块 JSON 才能确认发生了委派。每个委派步骤包含：

- `delegations`：委派次数；
- `coordinator_rounds`：协调器轮数；
- `specialists_used`：实际使用的子 Agent；
- `tools_used` / `tool_calls`：实际 MCP 工具和调用次数；
- 子 Agent 名称、收到的任务、实际工具、工具调用数、耗时和错误码；
- 每次 `search_notes` 的查询词、`top_k`、调用序号、去重数量和调用上限；
- 实际注入模型的裁剪后上下文、估算 token、原始 token 与 `truncated`；
- `evidence` / `citations_valid` / `citation_coverage`：知识证据与逐结论覆盖结果。

完整 `delegation_log` 仍保留在 Agent 内部结果中；Web 会把它转换为独立步骤后再写入 Task Trace。

评测器在配置 Provider 后会根据 `--mode` 真实创建 `ToolCallingAgent` 或 `DelegatedAgent`，不再
只改变报告标签。无 Provider 时仅允许 delegated 确定性回归。修复前生成的历史 single 报告
仍是无效模式标签，必须用相同 Provider、模型、任务集、策略和预算重新生成后才能比较。

## 本次验证结果

- Ruff：通过。
- pytest：`66 passed, 1 skipped`；跳过项是本机条件不满足的 Docker 测试。
- 工具文档一致性：`scripts/generate_tools_doc.py --check` 通过。
- 前端构建：Vite 构建成功，46 个模块完成转换。
- 新增测试覆盖：协调器只能看到五个委派函数；知识子 Agent 只能看到并实际调用
  `search_notes`；最多三次检索；证据按 `path:line` 去重；漏引触发修订；Web 返回真实查询和
  注入上下文，且响应不泄露 API Key。

## 仍然存在的边界

- 五个角色是代码预定义的，不会动态创建新角色。
- 当前顺序委派，不做并行执行、递归委派或跨对话语义长期记忆；只注入当前 conversation_id
  的最近消息。
- 多个角色共用一个 Provider，但系统提示、消息上下文和工具权限相互分离。
- `ci_status` 仍只读取状态，不提供完整 job/step 日志，因此不能凭空定位所有 CI 根因。
- 本地 `run_command` 仍是受限执行；只有 Docker 模式提供更强隔离。

