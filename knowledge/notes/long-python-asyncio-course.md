# Python asyncio 与服务端并发长笔记

## 事件循环的职责

asyncio 的事件循环负责调度协程、计时器和 I/O 就绪事件。协程执行到 await 时主动让出控制权，事件循环才能运行其他任务。async 并不会自动让同步代码变快：在协程中直接调用耗时的文件读取、subprocess.run 或 urllib.request，仍会阻塞整个事件循环。学习服务常用 asyncio.to_thread 把短小同步操作放到线程池，但线程数、取消传播和共享状态仍要考虑。高并发 HTTP 更适合原生异步客户端与连接池。

## Task 与取消

asyncio.create_task 把协程安排为可并发任务。取消不是强制终止线程，而是在下一个可取消点向协程抛 CancelledError。代码应在 finally 中释放文件、连接和锁，并通常重新抛出 CancelledError。后台任务需要保存引用，否则异常可能直到垃圾回收才被报告。任务 API 的 cancel 只能保证发出取消请求；如果当前步骤正在不可取消的同步线程中，响应可能延迟，因此工具本身仍需要超时和进程终止机制。

## 超时的层次

网络连接超时、读取超时、单次工具超时和整条任务截止时间是不同层次。只给 subprocess 设置十秒，不代表 Agent 六步循环不会运行一分钟。asyncio.wait_for 可以限制协程等待，但它取消的是等待方；底层线程或操作系统子进程可能继续运行。命令超时必须显式杀进程树，HTTP 客户端应配置连接和读取超时，Verification Loop 还要计算退避时间是否会超过总 deadline。

## 锁与竞态

asyncio.Lock 只协调同一事件循环中的协程。两个服务进程、外部编辑器和另一个 Python 解释器不会共享这把锁。文件更新需要乐观并发控制：读取时返回 SHA-256，写入时比较 expected_sha256，冲突时拒绝覆盖。数据库写入使用事务和唯一约束。不要在持有 asyncio.Lock 时做长时间网络请求，否则同一资源的其他任务会一直排队；锁内只保留读取当前状态、校验和提交所需的最小临界区。

## 同步代码进入线程

to_thread 适合无法异步化的标准库调用，但要认识限制。线程中的函数不能被协程取消，只是调用方不再等待；函数仍可能修改文件。线程共享进程环境和内存，传入可变对象时要考虑数据竞争。线程池也可能被大量慢任务耗尽。对于 Git 查询，短命 subprocess.run 放线程可接受；对于持续输出命令，应使用 Popen 或 asyncio subprocess 逐步读取，才能在达到输出上限时及时终止。

## 子进程输出与死锁

子进程 stdout/stderr 使用管道时，若父进程只读取一个管道，另一个管道缓冲区可能填满，双方互相等待。communicate 会同时处理两个管道，但会把全部输出放入内存。受限执行器可以为两个流各启一个读取线程，按块读取并限制累计字节；达到上限就标记 truncated 并终止进程树。解码使用 UTF-8 errors=replace，防止非 UTF-8 字节让审计链路崩溃。stdout 与 stderr 应分开返回，exit_code 非零也可以是正常业务结果。

## Windows 与 POSIX 进程树

POSIX 可以让子进程成为新 session，再向进程组发送 SIGKILL。Windows 的 CREATE_NEW_PROCESS_GROUP 不等于自动递归终止，常需 taskkill /T /F 或 Job Object。测试必须让脚本再创建子进程，并确认父任务超时后子进程也消失。不同平台的路径、引号和可执行扩展名也不同，所以必须使用参数数组与 shell=False，不能把命令拼成一个字符串交给 shell。

## ContextVar

contextvars 适合在异步调用链传播 trace_id、caller 和 task_id，不需要每层函数显式增加参数。每个 Task 会获得上下文副本，普通协程切换不会串值。进入后台任务前设置 caller_scope，可以让 MCP 工具包装层记录“agent”；外部 Host 没有该上下文时使用默认值。把 ContextVar 当全局可变变量会出错，必须保存 token 并在 finally reset，避免同一个请求处理线程上的后续调用继承旧值。

## SQLite 并发

SQLite 单文件数据库适合本地服务。短连接和短事务简化并发；WAL 允许读者与写者更好地并行。每次工具调用插入一条 tool_call，再插 attempts，应放在同一个事务。连接设置 timeout，短暂锁冲突可归为 transient。不要在事务中等待模型或子进程，否则会长时间占锁。多进程高写入场景最终可能迁移 PostgreSQL，但学习项目使用 SQLite 能真实展示 schema、索引、查询与持久化。

## SSE 流

Server-Sent Events 是服务端向浏览器推送单向事件的简单协议。每条事件包含 event 行、data 行和空行，Content-Type 为 text/event-stream。任务执行可以推送 step_started、tool_call、retry、fallback、llm_usage 和 task_finished。客户端断开后服务端应停止生成器，但是否取消后台任务取决于产品语义。事件只是实时视图，完整 Trace 仍应持久化，避免浏览器刷新后丢失。

## 错误边界

最外层 API 可以捕获未知 Exception，转换成不含密钥的 internal 错误并记录 trace_id；内层业务函数不要到处 except Exception 后返回空数据，否则真实缺陷会被伪装成成功。分类器需要保留结构化 category/code/retryable。HTTPError 是 URLError 的子类，必须先捕获 HTTPError 才能按状态码处理。subprocess.TimeoutExpired 不继承 TimeoutError，分类函数要显式包含。

## 测试异步代码

pytest-asyncio 能运行 async 测试。测试 Verification Loop 时注入第一次超时、第二次成功的操作，并把随机函数设为固定值，避免测试不稳定。测试取消要使用 asyncio.Event 协调，而不是依赖 sleep 猜时序。测试 SSE 应读取到终止事件。Windows 沙箱里事件循环会创建本地 socketpair，因此测试环境可能需要允许 loopback；这属于测试运行权限，不应误判为业务代码死锁。

## 资源清理

文件临时写入要在 finally 删除；HTTP response 使用上下文管理器；SQLite connection 用 with；子进程无论超时还是输出过大都要 wait 回收；后台任务在应用关闭时应取消并 await。只调用 kill 不 wait 会留下僵尸进程。回滚失败本身也要进入结果 meta，让调用方知道最终状态不确定。清理代码应可重复执行，例如 missing_ok=True，避免第二个异常覆盖第一个根因。

## 性能测量

time.perf_counter 适合进程内耗时，UTC ISO 时间适合跨事件排序。工具 duration_ms 应覆盖业务执行与验证，而 attempt duration 只覆盖单次尝试。P50 描述典型体验，P95 暴露长尾；平均值容易被极端值影响。异步并发压测还要观察事件循环延迟、线程池队列、数据库锁等待和输出截断次数。优化前先确认瓶颈，避免把磁盘扫描问题误当作模型问题。

## 设计总结

asyncio 的核心不是到处加 async，而是明确哪些等待可以并发、哪些同步边界需要隔离、取消与超时能否真正停止底层工作。可靠 Agent 服务要同时处理协程、线程、子进程、数据库和网络。每一层都有自己的生命周期，只有把 deadline、trace、最小权限和清理规则贯穿起来，系统才会在失败时保持可解释，而不是偶尔“看起来能跑”。
