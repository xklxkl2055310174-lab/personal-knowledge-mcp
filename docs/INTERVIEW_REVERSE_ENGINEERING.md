# Personal Knowledge MCP：项目反推与面试手册

> 目标：从当前真实代码反推出需求、架构、实现细节、设计取舍与面试表达。所有结论均以仓库代码为准，不把规划中的能力说成已经完成。

## 0. 先记住项目的真实边界

### 已经实现

- 基于 MCP Python SDK v2 的可运行 Server。
- Streamable HTTP 与 stdio 两种传输方式。
- 5 个结构化 MCP 工具：笔记检索、文件访问、Git 查询、CI 状态、受限命令执行。
- 路径限制、命令白名单、超时、大小限制、原子写入和写后校验。
- 统一成功/失败返回协议与错误分类。
- Verification Loop 与有限重试。
- 三种上下文策略及 JSONL 指标记录。
- SearchAgent、SummaryAgent 和 KnowledgeAgent 的职责拆分。
- 单元测试、MCP 集成测试、HTTP 冒烟测试、GitHub Actions 和 Docker 配置。

### 目前没有实现

- 没有接入真实 LLM API；`SummaryAgent` 是确定性证据整理器。
- 没有向量数据库、Embedding 或语义检索；当前是词频加权检索。
- 没有网页聊天 UI。
- 没有 OAuth、租户隔离、限流和完整审计，因此不能直接暴露到公网。
- `run_command` 是受限命令执行器，不是容器/虚拟机级强隔离沙箱。
- Docker 配置已提供，但当前开发机没有 Docker，未在该机器上构建镜像。

面试时主动讲清边界，比把学习项目包装成生产系统更可信。

---

## 1. 项目怎么介绍

### 30 秒版本

> 我做了一个个人知识库 MCP Server，把笔记检索、文件读写、Git 查询、CI 状态和受限命令执行封装成 5 个结构化工具。Agent 通过 MCP Client 发现并调用工具，而不是直接访问系统。项目重点不只是“能调用”，还加入了路径隔离、命令白名单、统一错误语义、超时、原子写入、结果校验和有限重试，并通过单元测试、MCP 集成测试和真实 HTTP 冒烟测试验证完整链路。

### 2 分钟版本

> 这个项目解决的是 Agent 与本地数据、开发工具之间缺少统一安全边界的问题。架构上分成四层：MCP 协议层、工具业务层、安全与可靠性层、Agent 编排层。Server 使用 MCP Python SDK v2，部署传输使用 Streamable HTTP，本地 Host 也可以用 stdio。五个工具共享统一的 `ToolResult` 返回结构，让 Agent 能区分参数、权限、超时和瞬时错误，从而决定是否重试。文件工具只能访问配置的工作区，写入使用临时文件替换并重新读取校验；Git 工具只映射固定只读子命令；命令工具使用参数数组、`shell=False`、白名单、目录限制和超时。Agent 侧拆成检索与总结两个职责，并提供 full、relevant、summary 三种上下文策略，记录估算 token 和成功率。测试既覆盖业务逻辑，也使用 SDK 的 Client 验证工具发现和调用，还通过 Streamable HTTP 做端到端冒烟测试。

### 一句话定位

> 这是一个“安全工具边界优先”的 MCP Agent 学习项目，而不是一个单纯的聊天机器人。

---

## 2. 从需求反推架构

### 原始需求

1. Agent 能检索个人笔记。
2. Agent 能操作有限范围内的文件和开发工具。
3. 工具调用失败时，有明确语义并能有限恢复。
4. 长工具结果不能无限塞入上下文。
5. 能比较单 Agent 与职责拆分后的效果。
6. 项目可测试、可本地运行、可容器化部署。

### 架构拆分

```text
用户问题
  |
  v
KnowledgeAgent（编排）
  |-- SearchAgent ------ MCP Client ------ Streamable HTTP / stdio
  |                                           |
  |                                           v
  |                                      MCP Server
  |                                           |
  |                         +-----------------+------------------+
  |                         |        |        |        |         |
  |                      Search    File      Git      CI      Command
  |                         |        |        |        |         |
  |                         +--------+--- Security / Verification-+
  |
  |-- ContextBudgetManager
  |-- SummaryAgent
  v
带证据位置的回答 + 运行指标
```

### 模块与职责映射

| 模块 | 职责 | 面试关键词 |
| --- | --- | --- |
| `server.py` | 注册工具、传输、健康检查 | MCP、Schema、Streamable HTTP |
| `services.py` | 五个工具的业务实现 | 单一职责、可测试性 |
| `config.py` | 环境变量和目录边界 | 配置外置、十二要素 |
| `contracts.py` | 统一返回协议 | 结构化错误、稳定契约 |
| `security.py` | 路径约束与命令名规范化 | Path Traversal、最小权限 |
| `verification.py` | 执行、验证、记录、重试 | Verification Loop、退避 |
| `context.py` | 上下文预算与成本指标 | token budget、裁剪 |
| `agent.py` | 子 Agent 与编排 | 委派、证据约束 |
| `demo.py` | MCP Client 演示 | 端到端调用 |
| `tests/` | 单元与集成验证 | 回归、隔离测试 |

---

## 3. 一次请求是怎么走完的

以“查询 MCP 为什么适合 Agent”为例：

1. `demo.py` 使用 `Client("http://127.0.0.1:8001/mcp")` 建立 MCP 连接。
2. `KnowledgeAgent.answer()` 委派给 `SearchAgent`。
3. `SearchAgent` 调用 MCP 工具 `search_notes`，参数是 `query` 和 `top_k`。
4. Server 根据装饰器生成的 Schema 校验输入，并转发到 `KnowledgeServices.search_notes()`。
5. Service 扫描 `knowledge/notes`，计算每一行的匹配分数并返回 Top-K。
6. SDK 把 `ToolResult` 序列化成 MCP 结构化结果。
7. `SearchAgent` 保留 `path:line`，形成证据块。
8. `ContextBudgetManager` 在 token 预算内选择或压缩证据。
9. `SummaryAgent` 只整理给定证据，不自行访问文件。
10. `MetricsRecorder` 把策略、估算 token 和成功标记写入 JSONL。

面试时要强调：Agent 与工具之间不是 Python 函数直连，而是经过 MCP Client/Server 边界；测试中同时存在“进程内 MCP Client”与“真实 HTTP MCP Client”两种验证方式。

---

## 4. MCP Server 如何实现

核心代码在 [`server.py`](../src/knowledge_mcp/server.py)。

### 4.1 Server 工厂

`create_server(settings)` 负责：

1. 加载或接收配置。
2. 构造 `KnowledgeServices`。
3. 创建 `MCPServer`。
4. 使用 `@server.tool(structured_output=True)` 注册工具。
5. 注册 `/health` 普通 HTTP 路由。

使用工厂而不是只创建全局单例，主要为了测试：测试可以传入临时目录配置，避免访问真实笔记和仓库。

### 4.2 类型标注的作用

工具参数和 `ToolResult` 返回类型不仅帮助 IDE，也被 SDK 用于生成工具 Schema。MCP Client 可以先发现工具和 Schema，再决定如何调用。

### 4.3 两种传输

- `stdio`：Host 启动本地子进程，通过标准输入输出传输 JSON-RPC，适合桌面工具。
- `streamable-http`：Server 监听 HTTP 端口，适合独立服务和容器部署。

代码通过 `MCP_TRANSPORT` 切换；HTTP 端点固定为 `/mcp`。

### 4.4 为什么根路径返回 404

项目是协议服务，不是网站。只定义了 `/health` 和 `/mcp`，因此访问 `/` 或 `/favicon.ico` 返回 404 是预期行为。

### 4.5 为什么使用 stateless HTTP

当前工具调用不依赖服务端会话状态，`stateless_http=True` 可以降低会话管理复杂度，也更适合后续横向扩展。需要客户端回调、长会话状态或订阅时则要重新评估。

---

## 5. 统一返回协议

核心代码在 [`contracts.py`](../src/knowledge_mcp/contracts.py)。

成功：

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "meta": {}
}
```

失败：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "path_not_allowed",
    "message": "Path escapes the configured root",
    "category": "permission",
    "retryable": false,
    "details": {}
  },
  "meta": {}
}
```

### 为什么不用一律抛异常

- 参数错误和权限拒绝是可预期业务结果，不应该伪装成服务崩溃。
- Agent 需要稳定字段判断下一步，而不是解析异常字符串。
- `retryable` 可以直接表达恢复策略。
- `meta` 可以放验证次数、耗时、是否截断等横切信息。

### 五类错误

| 分类 | 示例 | 是否自动重试 |
| --- | --- | --- |
| `parameter` | 空查询、非法 limit | 否 |
| `permission` | 路径越界、命令不在白名单 | 否 |
| `timeout` | 命令执行超时 | 可以有限重试 |
| `transient` | 网络暂时失败、OS 临时错误 | 可以有限重试 |
| `internal` | 未预期实现错误 | 不应盲目重试，应告警 |

---

## 6. 工具一：笔记检索

代码：`KnowledgeServices.search_notes()`。

### 输入

- `query: str`
- `top_k: int`，范围 1 到 20。

### 执行流程

1. 去除首尾空白，拒绝空查询。
2. 正则提取中英文词元并做 `casefold()`。
3. 递归扫描 `knowledge/notes`。
4. 只处理 `.md`、`.txt`、`.rst`。
5. 跳过无法解码或超过大小限制的文件。
6. 逐行计算匹配分数。
7. 按分数倒序、路径和行号稳定排序。
8. 返回 Top-K、总匹配数和是否截断。

### 当前评分公式

```text
score = 行内词元出现次数
      + 文件名命中词元数 × 2
      + 完整查询短语命中 × 3
```

### 为什么返回行号

- 回答可以保留证据位置。
- 调试检索结果更容易。
- 后续可以按 `path + line` 二次读取上下文窗口。

### 复杂度

设文件总文本量为 `N`，查询词元数为 `Q`，当前实现近似 `O(N × Q)`。个人知识库规模可以接受；规模增大后应引入倒排索引、BM25 或向量检索。

### 面试时不要说错

当前不是向量检索，也没有 Embedding；它是可解释的关键词加权检索。

---

## 7. 工具二：文件访问

代码：`KnowledgeServices.file_access()`。

### 支持操作

- `list`：列出目录，最多返回 200 项。
- `read`：读取 UTF-8 文本，受文件大小限制，并返回内容的 SHA-256。
- `write`：支持 `create`/`overwrite`，可用 `expected_sha256` 检测版本冲突，原子写入并做结果验证。
- `replace`：只替换一次唯一匹配的 `old_text`，可用 `expected_sha256` 防止覆盖并发修改。

### 路径安全

`resolve_inside(root, relative_path)`：

1. 拒绝绝对路径。
2. 将 `root / relative_path` 解析为规范真实路径。
3. 使用 `os.path.commonpath()` 判断结果是否仍在根目录内。
4. 不在根目录内则返回 `permission`。

因此 `../outside.txt`、跨盘路径和指向外部的已存在符号链接都会被阻止。

### 为什么使用原子写入

写入步骤：

```text
写临时文件 -> temporary.replace(target) -> 重新读取 -> 比较内容、字节数与 SHA-256
```

直接覆盖目标文件时，进程中断可能留下半个文件。先完整写临时文件，再使用同文件系统替换，可以缩短目标文件处于不一致状态的窗口。

### 并发控制

同一服务进程内使用路径级异步锁串行化同一文件的写入。跨进程或外部编辑器修改则使用乐观并发控制：先 `read` 获取 SHA-256，再把它作为 `expected_sha256` 提交；哈希不一致时返回不可重试的 `conflict/file_changed`，调用方必须重新读取并决策。它不是操作系统级文件锁，检查和替换之间仍存在很小的跨进程竞争窗口；生产环境可进一步使用平台文件锁或带版本号的存储服务。

---

## 8. 工具三：Git 查询

代码：`KnowledgeServices.git_query()`。

### 只开放固定映射

| operation | 实际命令 |
| --- | --- |
| `status` | `git status --short --branch` |
| `branch` | `git branch --show-current` |
| `log` | `git log -N --pretty=... --date=short` |
| `diff` | `git diff --stat` |

用户不能提交任意 Git 子命令，因此不会通过这个工具执行 `reset`、`clean`、`push` 等写操作。

### 为什么用 `asyncio.to_thread`

`subprocess.run()` 是阻塞调用。如果直接在异步工具函数中运行，会阻塞事件循环。`asyncio.to_thread()` 把阻塞工作放到线程，Server 仍可处理其他异步任务。

### 结果验证

- 返回码必须为 0。
- stdout 不得超过 100,000 字符。
- 失败会进入有限 Verification Loop。

---

## 9. 工具四：CI 状态

代码：`KnowledgeServices.ci_status()`。

### local 模式

- 扫描 `.github/workflows/*.yml` 和 `*.yaml`。
- 如果存在 `.ci/status.json`，解析最近一次本地状态。
- 状态文件无效时返回 `invalid`，而不是让整个工具崩溃。

### github 模式

- 参数必须是 `owner/name`。
- 调用 GitHub Actions Runs API。
- `GITHUB_TOKEN` 存在时通过 Authorization Header 注入。
- 401/403 分类为权限错误；5xx 和网络失败分类为瞬时错误。

### 可测试性设计

构造 `KnowledgeServices` 时可以注入 `urlopen`，因此测试网络逻辑时可以使用 fake opener，而不是访问真实 GitHub。这是依赖注入的一个小型应用。

### 生产改进

- 使用异步 HTTP Client 和连接池。
- 处理分页、Rate Limit Header 与 ETag 缓存。
- 使用 GitHub App/OAuth，避免长期 Personal Access Token。

---

## 10. 工具五：受限命令执行

代码：`KnowledgeServices.run_command()`。

### 防护措施

1. 命令必须是非空字符串数组。
2. 首个元素规范化为可执行文件名。
3. 可执行文件必须在 `MCP_ALLOWED_COMMANDS` 白名单中。
4. 工作目录必须位于 `workspace_root`。
5. 使用 `subprocess.run(list, shell=False)`，不让 shell 解释管道、分号和重定向。
6. 超时上限强制不超过 30 秒。
7. stdout/stderr 各最多返回 20,000 字符。
8. Python 命令替换为当前 `sys.executable`，避免跑到另一套环境。

### `shell=False` 为什么重要

如果把用户字符串拼成 shell 命令，`;`、`&&`、`|`、重定向等会获得额外语义。参数数组配合 `shell=False` 时，每个参数原样传给目标程序，显著缩小命令注入面。

### 必须承认的限制

这不是操作系统级强沙箱。白名单中的 Python 或 pytest 本身仍能执行代码，程序参数也可能让白名单程序访问额外资源。真正的生产沙箱需要：

- 独立容器/虚拟机或低权限账户。
- 只读文件系统和精确挂载。
- 网络隔离。
- CPU、内存、进程数和执行时长配额。
- 系统调用过滤与审计。

面试中把它称为“受限命令执行器”比“绝对安全沙箱”更准确。

---

## 11. Verification Loop

代码：[`verification.py`](../src/knowledge_mcp/verification.py)。

### 抽象

`run_verified(operation, verifier)` 接收：

- 一个异步操作。
- 一个验证结果是否合法的函数。
- 最大尝试次数，默认 2。
- 退避时间，默认 0.05 秒递增。

### 状态记录

每次尝试记录：

- `number`
- `duration_ms`
- `outcome`

### 为什么不是所有错误都重试

参数错误、权限错误在进入 Verification Loop 之前就返回，因为重复相同输入不会改变结果。只有超时、OS 瞬时失败或结果校验失败才有恢复可能。

### 为什么文件写入适合重试

写入采用临时文件 + replace，重复执行在目标内容相同的情况下基本幂等。对于非幂等操作，例如付款或创建资源，不能直接套用相同重试策略，必须引入幂等键或去重机制。

### 当前实现可以改进的地方

- 增加异常分类策略，而不是固定捕获 `TimeoutError`/`OSError`。
- 使用带抖动的指数退避。
- 增加总截止时间和取消传播。
- 记录 trace ID，接入日志和指标系统。

---

## 12. 上下文管理

代码：[`context.py`](../src/knowledge_mcp/context.py)。

### token 估算

当前实现使用轻量近似：

```text
ASCII 字符约 4 个算 1 token
非 ASCII 字符约 1 个算 1 token
```

它适合离线比较策略，不代表真实模型账单。接入模型后应使用对应 tokenizer 或 API 返回的 usage。

### 三种策略

#### full

结果未超过预算时完整注入。优点是信息损失少，缺点是 token 成本高。

#### relevant

计算每个 chunk 对问题词元的命中次数，优先选择高相关 chunk，再按预算截断。它是当前默认策略。

#### summary

把结果整理成有限条短行再装入预算。当前是提取式压缩，不是 LLM 生成式摘要。

### 指标记录

`MetricsRecorder` 以 JSONL 记录：

- strategy
- input_tokens
- output_tokens
- success
- timestamp

`summarize()` 可以按策略统计运行数、成功数、累计 token 和成功率。

### 如何设计真实实验

准备固定问题集和期望证据，分别运行三种策略，比较：

- Recall@K 或证据命中率。
- 最终回答正确率。
- 输入/输出 token。
- P50/P95 延迟。
- 单次请求成本。

当前仓库只实现指标记录框架，没有声称已经完成大规模实验。

---

## 13. 子 Agent 委派

代码：[`agent.py`](../src/knowledge_mcp/agent.py)。

### SearchAgent

- 只持有满足 `ToolClient` Protocol 的对象。
- 调用 `search_notes`。
- 检查结构化结果。
- 输出保留 `path:line` 的证据字符串。

### SummaryAgent

- 不持有 Tool Client。
- 只接收 query 和已经打包的 context。
- 无证据时明确返回未找到。
- 当前只整理前 8 条证据。

### KnowledgeAgent

负责编排：

```text
SearchAgent.run
  -> ContextBudgetManager.pack
  -> SummaryAgent.run
  -> MetricsRecorder.record
```

### 为什么用 `Protocol`

Agent 不依赖具体 MCP Client 类，只要求对象有 `call_tool()`。这样更容易替换真实 Client、Fake Client 或其他兼容实现，符合依赖倒置思想。

### 是否算真正的 Agent

它具备工具调用和职责委派的 Agent 架构，但没有模型驱动的动态规划；调用顺序是确定性的。面试时可以称为“可替换 LLM Provider 的确定性 Agent 工作流”，不要声称模型已经自主选择工具。

### 接入真实 LLM 的方式

1. 定义 `LLMProvider.generate(messages, tools)` 接口。
2. 把 SummaryAgent 替换成模型实现。
3. 保留 MCP Client、上下文预算、错误分类和指标层。
4. 对模型输出实施引用校验，要求引用必须来自检索结果。
5. 为高风险工具增加审批策略。

---

## 14. 配置与目录隔离

代码：[`config.py`](../src/knowledge_mcp/config.py)。

重要目录被拆开：

| 目录 | 用途 | 权限倾向 |
| --- | --- | --- |
| `knowledge_root` | 个人笔记 | 搜索只读 |
| `workspace_root` | 文件与命令工作区 | 有限可写 |
| `repository_root` | Git 和本地 CI | 查询为主 |
| `runtime_root` | 指标与运行数据 | 可写 |

所有路径、命令白名单、文件大小和超时都可以通过环境变量覆盖。代码不硬编码用户 Token，`GITHUB_TOKEN` 只从环境变量读取。

---

## 15. 测试策略

### 单元测试

[`test_services.py`](../tests/test_services.py) 覆盖：

- 检索排序和行号。
- 非法 top_k。
- 文件写入、读取和路径越权。
- Git 只读查询。
- 本地 CI 状态。
- 命令白名单允许与拒绝。

[`test_context.py`](../tests/test_context.py) 覆盖上下文预算和指标聚合。

[`test_verification.py`](../tests/test_verification.py) 模拟第一次超时、第二次成功，验证有限重试。

### MCP 集成测试

[`test_mcp_integration.py`](../tests/test_mcp_integration.py)：

- 使用 SDK `Client(server)` 建立进程内 MCP 连接。
- 验证客户端恰好能发现 5 个工具。
- 验证 SearchAgent 到 SummaryAgent 的完整委派链路。

### HTTP 冒烟测试

[`smoke_http.py`](../scripts/smoke_http.py) 连接真实 `/mcp` 端点，执行工具发现，并调用全部五个工具。它验证的不只是业务函数，还包括网络传输、协议协商、序列化与工具路由。

### 当前测试结果

当前自动化测试为 11 项，最近一次运行全部通过。面试时只陈述实际运行结果，不虚构覆盖率百分比，因为仓库没有配置 coverage 门槛。

---

## 16. CI 与部署

### GitHub Actions

工作流执行：

1. Checkout。
2. 安装 uv 与 Python 3.12。
3. 锁定依赖安装。
4. Ruff 静态检查。
5. Pytest。
6. 后台启动 Streamable HTTP Server。
7. 等待 `/health`。
8. 运行真实 HTTP 冒烟测试。

这形成“静态检查 -> 单元/集成测试 -> 部署形态冒烟”的质量门。

### Dockerfile

- 基础镜像：`python:3.12-slim`。
- 安装 Git 和 ripgrep。
- 安装项目 wheel。
- 使用非 root 用户 `mcp`。
- 提供 `/health` 容器健康检查。

### Compose

- 端口只绑定 `127.0.0.1`，默认不暴露到外网。
- `knowledge` 只读挂载。
- `workspace` 与 `.runtime` 可写持久化。
- `.git` 只读挂载，使 Git 查询在容器内可用。

### 为什么本地 PyCharm 使用 8001

当前 Windows 开发机的 8000 端口被其他进程占用，所以顶层 `main.py` 与 PyCharm 运行配置默认使用 8001。通用 Server、Docker 和 CI 仍默认使用 8000。这是运行环境配置差异，不是协议差异。

---

## 17. 核心设计权衡

### 为什么先做关键词检索而不是向量数据库

- 零额外服务，容易部署和测试。
- 排序规则可解释。
- 适合验证 MCP 工具边界。
- 缺点是同义词、语义召回和大规模性能较弱。

### 为什么业务逻辑不直接写在装饰器函数里

- Service 可以脱离协议单测。
- Server 文件只承担协议适配。
- 将来可复用相同 Service 到 CLI、REST 或后台任务。

### 为什么统一返回结构

- Agent 能稳定决策。
- 错误语义不会依赖字符串匹配。
- 容易添加 trace、耗时、截断状态。

### 为什么选择 stdlib urllib

- 当前 GitHub 查询很轻量，可以减少直接依赖。
- 通过 `to_thread` 避免阻塞事件循环。
- 如果请求规模增加，应切换异步 HTTP Client 与连接池。

### 为什么不自动重试所有工具

- 重试不是可靠性的万能药。
- 参数/权限问题不会因为重试而恢复。
- 非幂等操作可能被重复执行。
- 必须同时考虑错误类别、幂等性和截止时间。

---

## 18. 面试高频问答

### Q1：MCP 和普通 REST API 有什么区别？

REST 是通用资源接口风格；MCP 面向模型/Agent 场景，标准化了工具、资源、Prompt 的发现和调用，以及输入输出 Schema。REST 也能实现相同业务，但 Host 需要自定义工具描述、发现和适配逻辑。这个项目仍然通过 HTTP 传输，但在线路上说的是 MCP 协议。

### Q2：MCP 和模型 Function Calling 有什么区别？

Function Calling 描述模型如何选择结构化函数；MCP 描述 Host/Client 如何发现和调用外部能力。二者可以组合：模型选择工具，Host 通过 MCP Client 执行。

### Q3：为什么不是让 Agent 直接读文件？

直接访问会把权限、数据格式和异常处理散落在 Agent 代码中。MCP Server 把能力变成显式工具，可以集中实施最小权限、校验、审计和复用。

### Q4：如何防止路径穿越？

拒绝绝对路径，把相对路径解析成规范真实路径，再用 `commonpath` 检查结果仍位于配置根目录。不能只检查字符串里是否包含 `..`，因为符号链接、盘符和路径规范化会绕过简单字符串判断。

### Q5：如何防止命令注入？

使用参数数组、`shell=False`、可执行文件白名单、工作目录限制、超时和输出限制。但这仍不是强沙箱；生产环境还需 OS/容器隔离。

### Q6：为什么异步函数里还用了线程？

文件、urllib 和 subprocess 是同步阻塞 API。`asyncio.to_thread` 防止它们阻塞事件循环，同时避免为学习项目引入更多异步库。

### Q7：为什么写完文件还要读回来？

“没有异常”只能证明调用完成，不能证明目标状态正确。重新读取并比较内容和字节数，验证业务后置条件。

### Q8：Verification Loop 与普通 retry 有什么区别？

普通 retry 常以异常为依据；Verification Loop 还验证成功返回值是否满足后置条件。操作返回但结果错误时也能识别失败。

### Q9：重试会不会造成重复执行？

会，所以要判断幂等性。当前文件 replace 到相同内容和 Git 查询基本幂等；未来对创建、付款或发送消息等操作必须使用幂等键或禁止自动重试。

### Q10：如何保证工具输出不撑爆上下文？

工具本身限制结果数量和输出长度；Agent 层再通过 ContextBudgetManager 按 token 预算选择、排序或摘要。

### Q11：token 估算准确吗？

不完全准确，它只用于离线相对比较。接入具体模型后应使用真实 tokenizer 或 API usage。

### Q12：子 Agent 的价值是什么？

把检索和总结拆开后，可以分别测试召回质量与生成质量，限制总结器的数据来源，也便于替换实现和统计成本。

### Q13：当前是一个真正自主规划的 Agent 吗？

当前是确定性 Agent 工作流：工具调用与委派结构真实存在，但没有模型动态规划。下一步可以接入 LLM Provider，让模型基于工具 Schema 选择工具，同时保留现有安全和验证层。

### Q14：为什么使用工厂创建 Server？

便于注入测试 Settings，每个测试使用临时目录，避免依赖真实机器状态；也方便未来创建不同配置的实例。

### Q15：如何测试 MCP，而不是只测试 Python 函数？

一层测试 Service；一层使用 `Client(server)` 验证 MCP 工具发现和调用；最后一层连接真实 Streamable HTTP 端点做冒烟测试。

### Q16：如果笔记有十万篇怎么办？

当前每次全量扫描不合适。可以建立增量索引，使用 BM25/倒排索引或向量数据库；通过文件变更事件更新索引，并对检索加缓存和分页。

### Q17：如何减少幻觉？

保留 `path:line` 证据，只允许 SummaryAgent 使用给定上下文；接入 LLM 后增加引用合法性校验、无证据拒答和评测集。

### Q18：为什么不用数据库保存指标？

学习项目使用 JSONL，追加简单、可读、无外部依赖。多进程、高并发和查询需求出现后应迁移到 SQLite、时序数据库或可观测性平台。

### Q19：如何上生产？

补 OAuth/TLS、租户隔离、速率限制、审计日志、密钥管理、强沙箱、异步 HTTP、监控告警、负载测试和灰度发布，并按工具风险增加人工审批。

### Q20：这个项目最难的地方是什么？

可以回答：最难的不是注册工具，而是定义安全边界和失败语义。比如参数错误不能重试、路径必须在规范化后检查、命令工具不能宣称绝对安全、成功返回还要验证后置条件。

---

## 19. 面试追问中的危险表达

| 不建议说 | 建议说 |
| --- | --- |
| “我实现了完整 RAG” | “当前实现关键词检索，架构可替换为 BM25/向量检索” |
| “命令在沙箱里绝对安全” | “实现了白名单和目录限制，生产仍需容器/OS 隔离” |
| “Agent 会自主规划所有任务” | “当前是确定性编排，预留了 LLM Provider 替换点” |
| “token 成本下降了 60%” | “已实现记录和比较框架，尚未完成大样本统计” |
| “已经生产部署” | “本地 HTTP 已验证，提供 Docker/CI 配置，公网生产能力尚需鉴权等补强” |
| “所有错误都会自动恢复” | “只对可能恢复且基本幂等的错误有限重试” |

---

## 20. 现场演示脚本

### 第一步：启动

在 PyCharm 运行 `main.py`，看到：

```text
Uvicorn running on http://127.0.0.1:8001
```

### 第二步：健康检查

浏览器打开：

```text
http://127.0.0.1:8001/health
```

### 第三步：展示工具发现和真实调用

```powershell
uv run python scripts/smoke_http.py --url http://127.0.0.1:8001/mcp
```

说明输出中的五个工具是 MCP Client 发现的，而不是在脚本里直接调用 Service。

### 第四步：展示知识更新

在 `knowledge/notes/mcp-learning.md` 添加唯一事实，再执行：

```powershell
uv run knowledge-mcp-demo "刚添加的事实是什么？" --server http://127.0.0.1:8001/mcp
```

展示回答中的文件名和行号。

### 第五步：展示安全

```powershell
uv run pytest tests/test_services.py::test_file_access_writes_reads_and_blocks_traversal -v
```

解释为什么 `../outside.txt` 会被判为权限错误，而不是让文件系统直接处理。

### 第六步：展示可靠性

```powershell
uv run pytest tests/test_verification.py -v
```

解释第一次超时、第二次成功和 attempts 记录。

---

## 21. 反推学习顺序

### 第一遍：跑通

1. 运行 `main.py`。
2. 打开 `/health`。
3. 运行 HTTP 冒烟测试。
4. 修改笔记并重新查询。

### 第二遍：逐层阅读

1. `server.py`：MCP 工具如何暴露。
2. `contracts.py`：结果为什么结构化。
3. `services.py`：五个工具的真实逻辑。
4. `security.py`：边界怎么限制。
5. `verification.py`：失败如何恢复。
6. `context.py`：上下文如何裁剪。
7. `agent.py`：职责如何委派。

### 第三遍：用测试理解设计

每读一个模块，就找到对应测试；先预测测试为什么通过，再实际运行。

### 第四遍：自己重写

不看代码，尝试独立实现：

1. `ToolResult`。
2. `resolve_inside`。
3. 一个 `search_notes`。
4. 一个 Verification Loop。
5. 一个 MCP 工具注册。

真正能独立重写，才说明掌握，而不只是记住项目描述。

---

## 22. 可以继续补强的路线

### 第一阶段：真实 LLM

- 定义 Provider 接口。
- 接入一个模型。
- 强制引用证据。
- 增加失败回退和 token usage。

### 第二阶段：检索升级

- BM25 基线。
- Embedding 与向量索引。
- 混合检索和 rerank。
- 固定评测集。

### 第三阶段：安全与可观测性

- trace ID、结构化日志。
- 工具级权限和审批。
- 容器强隔离。
- 指标、告警、限流。

### 第四阶段：产品化

- Web/桌面聊天 UI。
- 多知识库管理。
- 增量索引。
- 用户与租户权限。

---

## 23. 最后背下这四句话

1. **MCP 解决的是 Agent 与外部能力之间的标准化发现和调用，不等于模型本身。**
2. **可靠工具调用不仅看有没有异常，还要验证结果是否满足后置条件。**
3. **安全的关键是缩小能力边界，而不是指望 Prompt 约束模型。**
4. **当前项目是可运行的确定性 Agent 工作流；真实 LLM、向量检索和生产鉴权是明确的下一步。**
