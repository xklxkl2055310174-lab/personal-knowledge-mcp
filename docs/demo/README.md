# 演示录制步骤

1. `uv sync --extra dev --locked`，运行 `uv run knowledge-mcp`。
2. 打开 `/health`，再运行 `uv run python scripts/smoke_http.py`，展示 tools/list 与 5 工具。
3. search_notes 查询“上下文预算怎么控制”，展示 heading/start_line/end_line/chunk_id。
4. file_access read→携带 SHA replace→再次 read；再演示 `../pyproject.toml` 被拒绝。
5. git_query 分别查 status/branch/log/diff，展示结构化字段与兼容 output。
6. ci_status 查 local；说明它只读工作流和 `.ci/status.json`，`unknown` 不代表失败；再说明
   GitHub 5xx/限流会重试并回退 stale。
7. run_command 运行 `examples/safe-command-demo.py`；展示 python -c 与 git commit 被拒。
8. 连接支持工具调用的 Provider，创建 delegated Task；先用 Git+CI 组合问题展示协调器顺序
   委派两个专职子 Agent，再用文件任务展示最小权限。切换 single，演示一个 Agent 直接选择工具。
   未连接 Provider 时只展示固定知识问答回退，并明确它不是动态委派。
9. 查询 `/api/tool-calls` 与 `/api/metrics/failures`，展示 trace_id 和脱敏参数。
10. 运行本地 delegated eval，明确报告标注“不可作简历模型数据”；配置真实 Key 后分别运行
    single/delegated，并核对报告确实记录不同执行器。只引用可追溯到报告的数据。

截图建议：工具列表、中文命中、越权错误、Git 结构化结果、Task Trace、失败分布、评测表。
