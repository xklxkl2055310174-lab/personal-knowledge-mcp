# 学习指南

建议按“能运行→能解释→能改测试→能独立实现”学习。

1. 启动服务，使用真实 MCP Client 运行 `scripts/smoke_http.py`。
2. 看 `server.py`：tools/list Schema、annotations、统一包装和 isError。
3. 看 `services.py`：逐个画出输入校验、执行、验证、分类、重试/回退。
4. 看 `index.py`：Markdown 分块、中文 bigram、增量索引和证据验证。
5. 看 `agent.py`：确定性 delegated 与真实 Provider single tool loop 的区别。
6. 看 `telemetry.py`：Task→Step→ToolCall→Attempt 与脱敏。
7. 逐个运行对应测试，先预测结果；再主动制造路径越权、超时、冲突和限流。
8. 跑三种 strategy 和两种 mode 的评测，只引用 reports 中真实数字。

面试时先说边界：本地命令是受限执行，Docker 才是隔离；本地评测不是模型效果；
中文检索是稀疏基线，不冒充完整向量 RAG。然后用代码和测试证明工具契约、MCP Client
边界、Verification Loop、引用校验和调用 Trace。
