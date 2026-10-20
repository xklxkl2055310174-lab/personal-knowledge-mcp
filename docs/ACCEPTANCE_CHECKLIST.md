# 验收清单

## 自动化

- [ ] `uv sync --extra dev` 成功。
- [ ] `uv run ruff check src tests scripts` 无错误。
- [ ] `uv run pytest` 全部通过。
- [ ] `GET /health` 返回 HTTP 200 和 `status=ok`。
- [ ] MCP Client 能发现恰好 5 个工具。
- [ ] `uv run python scripts/smoke_http.py` 通过真实 HTTP 端点调用全部工具。

## 工具行为

- [ ] `search_notes` 返回文件、行号、分数和片段。
- [ ] `file_access` 写入后再次读取内容一致。
- [ ] `file_access ../x` 返回 `permission`。
- [ ] `git_query status` 不修改仓库。
- [ ] `ci_status local` 能列出 `.github/workflows`。
- [ ] `run_command python -c ...` 成功，非白名单命令被拒绝。
- [ ] 超时和瞬时错误最多按策略有限重试。

## Agent 行为

- [ ] SearchAgent 只负责检索。
- [ ] SummaryAgent 只使用给定证据。
- [ ] 三种上下文策略不超过预算。
- [ ] `.runtime/agent_metrics.jsonl` 记录策略、token 近似值和成功率。

## 部署安全

- [ ] 默认端口只绑定 `127.0.0.1`。
- [ ] 未配置 OAuth/TLS 时不暴露到公网。
- [ ] 知识库、可写工作区、代码仓库使用不同根目录。
- [ ] 私有仓库 Token 只通过环境变量注入，不写入仓库。
