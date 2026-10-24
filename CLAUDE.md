# Claude Code 使用规范

先阅读 `AGENTS.md`、`docs/DESIGN.md` 和 `docs/TOOLS.md`。使用项目的
`.mcp.json` 连接后，先 `tools/list`，再按工具 description 选择调用。

文件写入先 read 获取 SHA-256，write/replace 时传 `expected_sha256`；conflict 后重新读取，
不得原样重试。parameter、permission 不重试；timeout、transient 遵守返回的 retryable。
命令只运行 workspace 内脚本，禁止尝试 `python -c` 或 Git 写子命令。

完成修改前运行验收命令，并把真实结果写入答复。不得声称没有运行过的测试通过。
