# 可观测性与 Trace

一次 Agent 任务可以建模为 task → step → tool_call → attempt。Task 保存问题和最终状态；Step 表示规划、检索、总结与回写；ToolCall 记录脱敏参数；Attempt 记录每次执行、验证与重试。

聚合指标要保留维度：工具、错误分类、调用来源、时间窗口。只有总成功率无法回答“哪个工具最不稳定”或“超时主要发生在哪个调用方”。
