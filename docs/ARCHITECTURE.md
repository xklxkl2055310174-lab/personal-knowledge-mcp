# 架构说明

## 数据流

```text
用户问题
  -> KnowledgeAgent（编排）
      -> SearchAgent
          -> MCP Client
              -> search_notes 工具
                  -> knowledge/notes
      -> ContextBudgetManager（full / relevant / summary）
      -> SummaryAgent
  -> 带证据位置的回答 + token 成本记录
```

其余 4 个工具通过同一 MCP 边界提供给任何兼容 Host。Agent 不直接操作文件、Git、网络或进程。

## 为什么拆成四层

1. `server.py` 是协议适配层，只注册工具和传输。
2. `services.py` 是业务层，可脱离 MCP 单测。
3. `security.py` 与 `verification.py` 是横切层，集中处理边界和可靠性。
4. `agent.py` 是编排层，只消费结构化工具结果。

这种拆法让错误更容易定位：协议问题看 Server，工具行为看 Service，越权看 Security，推理/上下文问题看 Agent。

## Verification Loop

文件写入和 Git 查询的流程是：

```text
校验输入 -> 执行 -> 校验输出 -> 成功返回
                       |
                       +-> 超时/瞬时错误 -> 有限重试 -> 仍失败则回退
```

参数错误和权限错误不重试，因为相同输入不会自行变正确。每次尝试记录耗时和结果，返回在 `meta.attempts` 中。

## 安全模型

- 文件路径必须是相对路径，解析后的真实路径必须仍位于沙箱根目录。
- 命令使用参数数组和 `shell=False`，因此不会解释 `;`、管道或重定向。
- 命令名必须在白名单中，工作目录也必须在沙箱内。
- Git 工具仅暴露固定只读子命令。
- HTTP 服务默认仅监听 `127.0.0.1`；Docker 也只发布到回环地址。

这是学习项目的最小安全基线，不等同于多租户生产沙箱。生产环境还应使用进程/容器隔离、只读文件系统、最小权限账户、OAuth、审计与配额。

## MCP 传输选择

- `stdio`：本地 Host 启动子进程，最适合桌面开发。
- `streamable-http`：网络服务部署方式，本项目默认使用。
- SSE：旧方案，本项目不使用。

服务同时提供 `/health`，方便容器编排系统做存活检查。

## Web 控制台

```text
浏览器（Vue 3, web/）
  ├─ /mcp  JSON-RPC ──────────────> MCPServer 5 个工具（与任何 MCP Host 相同的协议面）
  ├─ /api/agent/*  ───> web.py ──> KnowledgeAgent ──in-memory MCP Client──> search_notes
  ├─ /api/notes*   ───> web.py ──> knowledge/notes（resolve_inside 防越权，只读）
  └─ /, /agent ... ───> web.py ──> web/dist（SPA history 回退）
```

- `web.py` 只是展示层适配器，不新增工具、不绕过 `security.py`：工作区读写仍由前端调用 `file_access` 完成。
- `KnowledgeAgent.answer_with_trace()` 记录五个阶段（intake / search / context / summary / writeback）的耗时与细节；`answer()` 复用它，原有 CLI 演示行为不变。
- `/api/agent/compare` 用同一问题和预算依次运行 full / relevant / summary，每次运行都写入 `.runtime/agent_metrics.jsonl`。
