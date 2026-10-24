# 项目协作约定

- Python 3.11+，MCP SDK 固定 2.0.1；新增依赖必须说明不可用标准库完成的理由。
- 工具保持 `ToolResult {ok,data,error,meta}`；错误要有 category、code 与 retryable。
- Agent 调工具必须经过 MCP Client，不直接 import Service 绕过协议边界。
- 路径必须相对授权 root，经 `resolve_inside` 校验；不得放开绝对路径。
- 本地 run_command 叫“受限执行”，Docker 模式才称隔离沙箱。
- 改代码后运行：`uv run ruff check src tests scripts`、`uv run pytest`、
  `uv run python scripts/generate_tools_doc.py --check`。
- 不提交 `.env`、`.runtime`、API Key、评测真实用户数据或完整大型数据集。
- 文档写中文，代码标识符写英文；行为变化同步测试、TOOLS/API/DESIGN。
