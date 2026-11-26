# 架构说明

数据流：

```text
Host / Web -> MCP Client -> MCPServer tool wrapper
  -> Service -> Security + Verification -> local/external system
  -> ToolResult + audit SQLite

Task -> single ToolCallingAgent -> model-selected MCP tools (最多 6 轮)
     or delegated Coordinator -> delegate_knowledge -> SearchAgent loop -> search_notes
                              -> delegate_files     -> FileAgent loop   -> file_access
                              -> delegate_git       -> GitAgent loop    -> git_query
                              -> delegate_ci        -> CIAgent loop     -> ci_status
                              -> delegate_command   -> CommandAgent loop-> run_command
        -> coordinator aggregation + citation validation

No provider -> offline delegated fallback -> SearchAgent(search_notes)
            -> ContextBudgetManager -> SummaryAgent(no tools) -> file_access write-back
```

目录根分离：

- knowledge_root：只读知识索引来源。
- workspace_root：file_access 与 run_command 授权区。
- repository_root：git_query 与本地 CI 查询根。
- runtime_root：索引、Trace、指标、备份。

四个根目录的权限互不继承。尤其是 search_notes 返回的路径相对 knowledge_root，不能直接交给
基于 workspace_root 的 file_access。ci_status 本地模式只读取工作流清单和可选的
`repository_root/.ci/status.json`，不执行 CI。

MCP Server 支持 stdio 与 Streamable HTTP。Web API 保留原接口并增加异步 Task/SSE；
前端直接调工具仍走 `/mcp`。工具注册层统一增加 trace meta、MCP isError 与 SQLite 审计。
协调器的“委派函数”不是绕过 MCP 的新工具；它只负责选择专职子 Agent，子 Agent 的实际执行
仍全部经过 MCP Client。SQLite 因而能记录底层真实工具调用。

跨平台点：Windows 用 taskkill 终止进程树，POSIX 用 process group；路径统一先解析再
`commonpath`；Docker sandbox 在 Linux CI 实跑，本机无 Docker 时相关测试跳过。

详细取舍见 [DESIGN.md](DESIGN.md)，契约见 [TOOLS.md](TOOLS.md)。
能力边界与非目标见 [LIMITATIONS.md](LIMITATIONS.md)。
