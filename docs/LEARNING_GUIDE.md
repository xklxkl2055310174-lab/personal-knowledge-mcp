# 学习指南：从函数到 MCP Agent

## 第一关：理解 MCP 的角色

普通函数只能被同一进程中的代码调用。MCP 把函数的名称、描述、输入 Schema 和输出通过协议暴露给 Host。Host 可以先 `tools/list` 发现工具，再用 `tools/call` 调用。

本项目用 `MCPServer` 注册 Python 函数。类型标注生成输入/输出 Schema；统一的 `ToolResult` 让 Agent 不需要猜测异常文本。

练习：运行 `uv run pytest tests/test_mcp_integration.py -v`，然后在 `server.py` 给某个工具改名，观察发现测试如何失败。

## 第二关：工具不是“任意代码执行”

工具的价值来自边界清晰。`file_access` 只允许 `workspace` 下的相对路径；`run_command` 不经过 shell，并且限制命令、目录、超时和输出长度。

练习：

```python
await services.file_access("read", "../secret.txt")
await services.run_command(["powershell", "-Command", "Get-ChildItem"])
```

两者都应得到 `permission`，而不是程序崩溃。

## 第三关：建立 Verification Loop

“进程返回 0”不总等于任务完成。本项目在写文件后重新读取并比对内容，在 Git 查询后校验返回码和输出大小。

失败分成五类：

- `parameter`：调用参数错误，修改参数后再调用。
- `permission`：权限或沙箱边界，必须由人或策略改变授权。
- `timeout`：执行超过时间限制，可以有限重试。
- `transient`：网络或系统暂时失败，可以退避后重试。
- `internal`：实现缺陷，应记录并修复。

练习：阅读 `test_verification.py`，把第一次 `TimeoutError` 改成持续失败，观察最终报告。

## 第四关：管理上下文而不是堆上下文

`ContextBudgetManager` 支持三种策略：

- `full`：结果很短时全部注入，信息完整但成本最高。
- `relevant`：按问题词命中排序，优先放相关片段。
- `summary`：先压成长结果要点，成本最低但可能损失细节。

token 估算是离线近似值，用于相对比较；接入真实模型后应替换为该模型的 tokenizer 或 API usage。

运行：

```powershell
uv run knowledge-mcp-demo "MCP" --strategy relevant
uv run knowledge-mcp-demo "MCP" --strategy summary
```

然后查看 `.runtime/agent_metrics.jsonl`。关注输入 token、输出 token、成功率，而不只看答案是否“像真的”。

## 第五关：子 Agent 委派

`SearchAgent` 只检索证据，`SummaryAgent` 只根据给定证据总结。拆分后更容易分别测试准确率、耗时和成本，也能避免总结器偷偷绕过工具访问数据。

当前总结器是确定性实现，因此无需 API Key。下一步可定义 `LLMProvider` 接口，让总结器调用任意模型。保留证据位置、上下文预算和指标记录，这些比更换模型更重要。

## 第六关：从本地到部署

本地开发可以用 stdio；部署使用 Streamable HTTP。Docker Compose 挂载三类持久目录：

- `knowledge`：只放个人笔记。
- `workspace`：允许文件工具和命令工具修改。
- `.runtime`：保存 Agent 运行指标。

对外部署前必须补 OAuth/TLS。不要把当前无鉴权端点直接映射到公网。

## 推荐扩展顺序

1. 给检索增加 BM25 或向量索引，并保留当前纯文本实现作回退。
2. 接入真实 LLM，总结时强制引用 `path:line`。
3. 给工具调用增加审计日志与 trace ID。
4. 用 GitHub App/OAuth 代替长期 Personal Access Token。
5. 在容器级限制 CPU、内存、网络和只读挂载。
