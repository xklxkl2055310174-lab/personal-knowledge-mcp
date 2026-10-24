# 验收清单

## 自动化

- [ ] `uv sync --extra dev --locked`
- [ ] `uv run ruff check src tests scripts`
- [ ] `uv run pytest -m "not docker"`
- [ ] Docker 可用时 `uv run pytest -m docker`
- [ ] `uv run python scripts/generate_tools_doc.py --check`
- [ ] `uv run knowledge-mcp-eval --mode delegated --strategy relevant`
- [ ] HTTP Server 启动后 `uv run python scripts/smoke_http.py`

## 工具契约与日志

- [ ] tools/list 恰好 5 个工具，每个参数有 description，枚举和 annotations 正确。
- [ ] ok=false 同时具有 structuredContent 且 MCP isError=true。
- [ ] meta 含 trace_id、tool、duration_ms、attempts、caller。
- [ ] SQLite 能按工具、错误分类、caller、时间查询；密钥和 content 已脱敏。

## 检索与上下文

- [ ] 三个中文自然句均命中；返回 heading/start_line/end_line/chunk_id。
- [ ] 修改/删除笔记后增量索引同步。
- [ ] 证据路径、行号和内容可回到原文。
- [ ] full/relevant/summary 使用不同来源，预算内裁剪。
- [ ] Provider usage 优先，估算值标记来源。

## 文件、Git 与 CI

- [ ] file_access 越权/绝对路径拒绝，SHA 冲突不重试。
- [ ] 写入前生成 backup_id；写后验证失败恢复原文。
- [ ] git status/log 返回结构化字段；非仓库 parameter；超时 timeout。
- [ ] GitHub 404/401/403/限流/5xx 分类正确；瞬时失败回退 local stale。

## 命令安全

- [ ] 子进程读不到 GITHUB_TOKEN。
- [ ] Python -c/-m、工作区外脚本、Git 写子命令均拒绝。
- [ ] 非零 exit_code 作为正常观察返回，不误判工具失败。
- [ ] 超时和输出超限终止进程树；命令不自动重试。
- [ ] Docker 模式无网络、非 root、资源受限；无 Docker 时本机测试跳过。

## Agent、API 与评测

- [ ] 单 Agent 通过模型 function calling + MCP Client 调工具。
- [ ] 委派模式检索子 Agent 权限受限，总结子 Agent 无工具。
- [ ] 引用只允许来自本轮证据，结果经 MCP file_access 写入 outputs。
- [ ] Task 历史、详情、取消、SSE 与各指标接口通过。
- [ ] 30 条评测使用临时 workspace；指标定义不是“检索非空”。
- [ ] 本地报告明确不可写入简历；真实模型数字可追溯到 reports 文件。
