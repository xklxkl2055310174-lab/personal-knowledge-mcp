# Personal Knowledge MCP

这是一个用于学习 MCP 与 Agent 工程的个人知识库项目。

项目提供以下 MCP 工具：

- search_notes：检索本地笔记
- file_access：在安全工作区内读取、写入和列出文件
- git_query：查询 Git 状态、分支、日志和差异
- ci_status：查询本地 CI 或 GitHub Actions 状态
- run_command：在命令白名单和超时限制下执行命令

当前知识问答 Agent 使用 search_notes 检索证据，经过上下文预算管理后，再交给大模型生成答案。
