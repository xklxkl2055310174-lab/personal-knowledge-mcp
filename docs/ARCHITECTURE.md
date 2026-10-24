# 架构说明

数据流：

```text
Host / Web -> MCP Client -> MCPServer tool wrapper
  -> Service -> Security + Verification -> local/external system
  -> ToolResult + audit SQLite

Task -> single tool-calling Agent
     or delegated SearchAgent -> ContextBudgetManager -> SummaryAgent
  -> citation validation -> MCP file_access result write-back
```

目录根分离：

- knowledge_root：只读知识索引来源。
- workspace_root：file_access 与 run_command 授权区。
- repository_root：git_query 与本地 CI 查询根。
- runtime_root：索引、Trace、指标、备份。

MCP Server 支持 stdio 与 Streamable HTTP。Web API 保留原接口并增加异步 Task/SSE；
前端直接调工具仍走 `/mcp`。工具注册层统一增加 trace meta、MCP isError 与 SQLite 审计。

跨平台点：Windows 用 taskkill 终止进程树，POSIX 用 process group；路径统一先解析再
`commonpath`；Docker sandbox 在 Linux CI 实跑，本机无 Docker 时相关测试跳过。

详细取舍见 [DESIGN.md](DESIGN.md)，契约见 [TOOLS.md](TOOLS.md)。
