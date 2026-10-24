# Personal Knowledge MCP

这是一个可运行、可测试、可容器化的 MCP 学习项目。它把个人笔记检索、文件读写、Git 查询、CI 状态查询和受限命令执行封装为 5 个结构化工具，并提供一个不需要 API Key 的双子 Agent 示例，以及一个 Vue 3 Web 控制台（总览 / Agent 问答 / 工具调试台 / 笔记与工作区）。

## 你会学到什么

- MCP Server 如何声明工具、输入 Schema、结构化返回值与 Streamable HTTP 传输。
- Agent 如何只负责规划与编排，把真实操作交给 MCP 工具。
- 如何用路径沙箱、命令白名单、超时和输出限制缩小工具权限。
- 如何实现“执行 -> 结果校验 -> 错误分类 -> 有限重试”的 Verification Loop。
- 如何比较完整注入、相关片段注入、长结果摘要三种上下文策略的 token 成本。
- 如何通过检索 Agent 与总结 Agent 的委派拆分职责。

详细原理见 [学习指南](docs/LEARNING_GUIDE.md)，组件关系见 [架构说明](docs/ARCHITECTURE.md)。如果你准备复盘实现或参加面试，直接阅读 [项目反推与面试手册](docs/INTERVIEW_REVERSE_ENGINEERING.md)。

## 五个 MCP 工具

| 工具 | 用途 | 关键保护 |
| --- | --- | --- |
| `search_notes` | 检索 `knowledge/notes` 中的 Markdown/Text | 文件大小、后缀、结果数量限制 |
| `file_access` | 在 `workspace` 中列目录、读取、写入和局部替换 | 路径沙箱、SHA-256 冲突检测、原子写入、写后校验 |
| `git_query` | 查询 status/branch/log/diff | 只读命令、仓库根目录限制 |
| `ci_status` | 读取本地 CI 信息或 GitHub Actions | 参数校验、超时、Token 可选 |
| `run_command` | 执行白名单命令 | `shell=False`、工作目录沙箱、超时、输出截断 |

每个工具都返回统一结构：

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "meta": {}
}
```

失败时 `error.category` 是 `parameter`、`permission`、`conflict`、`timeout`、`transient` 或 `internal`。Agent 只应重试 `timeout`/`transient`；遇到 `conflict` 应重新读取文件后再决策。

## 1. 本地启动（Windows / macOS / Linux）

要求 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --extra dev
uv run knowledge-mcp
```

在 PyCharm 中也可以直接右键运行项目根目录的 `main.py`；项目解释器仍需选择一次
`.venv/Scripts/python.exe`，因为 MCP SDK 安装在该虚拟环境中。

服务地址：

- MCP：`http://127.0.0.1:8000/mcp`
- 健康检查：`http://127.0.0.1:8000/health`

另开一个终端运行双子 Agent：

```powershell
uv run knowledge-mcp-demo "MCP 为什么适合连接 Agent 和工具？"
```

先编辑 `knowledge/notes/mcp-learning.md`，再提问，能直观看到检索结果变化。

## Web 控制台（前端）

前端位于 `web/`，技术栈 Vue 3 + Vite + vue-router，无第三方 UI 库。

| 页面 | 作用 |
| --- | --- |
| 总览 `/` | 服务信息、协议版本、`tools/list` 实时工具清单、运行配置、Server Instructions |
| Agent 问答 `/agent` | 选择 full / relevant / summary 策略和 token 预算，查看“任务接入 → 检索子 Agent → 上下文管理 → 总结子 Agent → 结果回写”五阶段链路、带出处的回答、预算占用、三策略成本对比和历史指标 |
| 工具调试台 `/tools` | 浏览器直接作为 MCP Client 调用 5 个工具，内置正常/错误示例，展示错误分类、是否可重试、Verification attempts 和原始 JSON-RPC |
| 笔记与工作区 `/files` | 知识库笔记带行号只读浏览（可从 Agent 证据跳转到对应行）；工作区通过 `file_access` 浏览、新建、编辑并原子写入 |

工具调试台的 `file_access` 支持浏览 `MCP_WORKSPACE_ROOT` 内的目录，并按 list/read/write/replace
模式选择目录或文件。文件选择器始终通过 MCP `file_access(action=list)` 获取目录内容，只显示
相对于安全工作区的路径；它不会放开绝对路径访问，后端仍使用 `resolve_inside` 阻止路径越权。
`workspace/examples/` 提供项目介绍、文件安全说明和 Coding Agent 文件工具演示任务。

### 安全文件更新演示

`read` 会在内容之外返回当前文件的 `sha256`。更新已有文件时，把该值作为
`expected_sha256` 传给 `write` 或 `replace`；如果文件在读取后被其他调用修改，工具返回不可
重试的 `conflict/file_changed`，不会覆盖新版本。`write` 的 `create` 模式只创建不存在的文件，
`overwrite` 模式保持原有覆盖语义。

```json
{
  "action": "replace",
  "path": "examples/project-overview.md",
  "expected_sha256": "从 read 返回结果复制的 64 位哈希",
  "old_text": "需要唯一命中的原文",
  "new_text": "替换后的文本"
}
```

`replace` 要求 `old_text` 在最新文件中恰好出现一次；未命中或多次命中都会拒绝写入。成功写入
仍采用“同目录临时文件 → 原子替换 → 重读内容与哈希”的 Verification Loop。所有路径继续受
`MCP_WORKSPACE_ROOT` 约束，绝对路径和 `../` 越权路径不会因为新操作而放开。

### 接入真实 LLM

点击左侧导航底部的“模型 API”，可以为总结子 Agent 选择真实模型。内置预设包括
OpenAI、Anthropic Claude、Google Gemini、DeepSeek、通义千问、Kimi、智谱 GLM、
火山方舟/豆包、百度千帆、腾讯混元、OpenRouter、SiliconFlow、Azure OpenAI、Ollama
以及自定义 OpenAI 兼容接口。模型名称与 Base URL 均可修改，以供应商控制台中的实际值为准。

API Key 仅保存在当前浏览器页面的 JavaScript 内存中：不会写入仓库、`.env`、
`localStorage` 或 `.runtime`，刷新页面后需要重新输入。点击“测试连接”会发起一次最小模型请求；
保存后，“Agent 问答”的检索仍通过 MCP `search_notes` 完成，但总结阶段会把预算后的证据交给
所选 LLM，并在执行链路中显示供应商、模型和供应商返回的 token 用量。没有配置时自动使用原来的
本地确定性总结器。

“三种策略对比”在真实 LLM 模式下会产生三次模型请求，请留意供应商费用。这个学习项目只应绑定
在 `127.0.0.1`；如果对外部署，必须把密钥管理改为服务端 Secret，并补充认证、TLS、审计和限流。

通信方式：

- 工具调用走真实 MCP 协议：浏览器向 `/mcp` 发送 JSON-RPC（`initialize` / `tools/list` / `tools/call`）。
- MCP 不覆盖的能力走辅助接口 `src/knowledge_mcp/web.py`：`GET /api/overview`、`POST /api/agent/ask`、`POST /api/agent/compare`、`GET /api/agent/metrics`、`GET /api/notes`、`GET /api/notes/content?path=`。Agent 仍通过 in-memory MCP Client 调用 `search_notes`，保持“Agent 只编排、MCP 负责执行”的边界。

开发模式（需要 Node 18+）：

```bash
uv run knowledge-mcp            # 终端 1：后端 8000 端口
cd web && npm install && npm run dev   # 终端 2：http://localhost:5173
```

Vite 会把 `/mcp`、`/api`、`/health` 代理到 `http://127.0.0.1:8000`。如果用 `python main.py` 启动（默认 8001），请复制 `web/.env.example` 为 `web/.env` 并设置 `VITE_BACKEND_URL=http://127.0.0.1:8001`。

生产模式：

```bash
cd web && npm install && npm run build   # 生成 web/dist
uv run knowledge-mcp                     # 访问 http://127.0.0.1:8000/
```

后端会自动托管 `web/dist`（可用 `MCP_WEB_DIST` 指定其他目录），并对前端路由做 history 回退。Docker 镜像已在构建阶段完成前端打包。

## 2. Docker 部署

```bash
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/health
```

Compose 只把端口绑定到本机回环地址。这个示例没有 OAuth，不应直接暴露到公网。生产环境应在 MCP 服务前增加 TLS、身份认证、审计和速率限制。

停止服务：

```bash
docker compose down
```

## 3. 测试与质量检查

```powershell
uv run ruff check src tests scripts
uv run pytest
uv run python scripts/smoke_http.py
```

测试覆盖 5 个工具发现、路径越权、命令白名单、Git/CI 查询、验证重试、上下文预算和子 Agent 委派。逐项人工验收见 [验收清单](docs/ACCEPTANCE_CHECKLIST.md)。

## 4. stdio 接入本地 MCP Host

环境变量设置 `MCP_TRANSPORT=stdio`，命令指向项目虚拟环境中的 `knowledge-mcp`。通用配置示意：

```json
{
  "mcpServers": {
    "personal-knowledge": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/personal-knowledge-mcp", "run", "knowledge-mcp"],
      "env": {"MCP_TRANSPORT": "stdio"}
    }
  }
}
```

不同 MCP Host 的配置文件位置不同，请以对应产品的最新文档为准。

## Git 查询工具演示

`git_query` 是只读 MCP 工具，支持 `status`、`branch`、`log` 和 `diff`。`repo_path` 必须填写相对于 `MCP_REPOSITORY_ROOT` 的路径；查询当前项目根目录时填写 `.`，不要传入绝对路径。

```json
{
  "operation": "status",
  "repo_path": ".",
  "limit": 10
}
```

`git_query` 与 `file_access` 使用不同的安全根目录：前者基于 `repository_root` 检查 Git 仓库，后者基于 `workspace_root` 读写授权工作区。Git 工具只运行预先定义的查询命令，不会执行 `commit`、`push`、`checkout`、`reset` 等修改仓库的操作。

## 配置

复制 `.env.example` 后按需设置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MCP_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `MCP_PORT` | `8000` | HTTP 端口 |
| `MCP_KNOWLEDGE_ROOT` | `./knowledge` | 笔记根目录 |
| `MCP_WORKSPACE_ROOT` | `./workspace` | 文件/命令工具沙箱 |
| `MCP_REPOSITORY_ROOT` | 项目根目录 | Git/本地 CI 只读根目录 |
| `MCP_ALLOWED_COMMANDS` | `python,python3,pytest,git,rg` | 命令白名单 |
| `GITHUB_TOKEN` | 空 | 私有仓库或更高 API 限额时使用 |
| `MCP_WEB_DIST` | `./web/dist` | Web 控制台静态文件目录（未构建时访问 `/` 显示构建提示） |

## 项目结构

```text
src/knowledge_mcp/
  server.py         MCP Server 与 5 个工具注册
  services.py       工具业务逻辑与安全边界
  verification.py   校验、失败分类、有限重试
  context.py        上下文预算与成本指标
  agent.py          检索/总结子 Agent 与编排
  demo.py           HTTP MCP Client 演示
  web.py            Web 控制台辅助接口 + 前端静态托管
web/                Vue 3 前端（src/views 四个页面，src/api/mcp.js 浏览器 MCP Client）
knowledge/notes/    个人知识库
workspace/          文件和命令沙箱
tests/              单元测试、MCP 集成测试与 Web 接口测试
```

## 学习建议

1. 先运行测试，再阅读 `server.py`，理解“协议层”和“业务层”的边界。
2. 给 `knowledge/notes` 增加三篇笔记，观察 `search_notes` 的排序。
3. 故意传入 `../secret.txt`，理解为什么工具返回 `permission` 且不重试。
4. 分别运行 `full`、`relevant`、`summary`，查看 `.runtime/agent_metrics.jsonl`。
5. 最后把 `SummaryAgent` 替换为你选择的 LLM，但保留 MCP、安全和验证层。
