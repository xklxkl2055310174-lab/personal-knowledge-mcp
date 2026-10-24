# 并发与乐观锁

进程内 asyncio.Lock 可以串行化同一服务实例内的写入，但挡不住另一个进程。跨进程冲突可让调用方先 read 获取 SHA-256，再在 write/replace 传 expected_sha256。

若当前哈希与预期不同，返回 conflict，让 Agent 重新读取和重新规划。冲突不是 transient，不应该原样自动重试。
