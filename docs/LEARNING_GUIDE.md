# 学习指南

建议按“能运行→能解释→能改测试→能独立实现”学习。

1. 启动服务，使用真实 MCP Client 运行 `scripts/smoke_http.py`。
2. 看 `server.py`：tools/list Schema、annotations、统一包装和 isError。
3. 看 `services.py`：逐个画出输入校验、执行、验证、分类、重试/回退。
4. 看 `index.py`：Markdown 分块、中文 bigram、增量索引和证据验证。
5. 看 `agent.py`：比较 single 的“一个 Agent 直接看到五个工具”与 delegated 的“协调器只看
   委派函数、五个子 Agent 各看一个工具”；再看无 Provider 时的固定知识问答回退链。
6. 看 `telemetry.py`：Task→Step→ToolCall→Attempt 与脱敏。
7. 逐个运行对应测试，先预测结果；再主动制造路径越权、超时、冲突和限流。
8. 无 Key 时跑 delegated 的三种 strategy 做确定性回归；配置相同 Provider/模型后，分别跑
   single 与 delegated。确认报告中的实际工具调用和委派日志后，才比较结果。

面试时先说边界：本地命令是受限执行，Docker 才是隔离；本地评测不是模型效果；
中文检索是稀疏基线，不冒充完整向量 RAG。然后用代码和测试证明工具契约、MCP Client
边界、Verification Loop、引用校验和调用 Trace。

完整限制清单见 [LIMITATIONS.md](LIMITATIONS.md)。
