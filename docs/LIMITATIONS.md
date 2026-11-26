# 能力边界与已知限制

本文只描述当前代码已经实现的能力，避免把演示骨架解释成生产级通用 Agent。

## 项目定位

项目核心是把本地知识、文件、Git、CI 和命令执行封装为具有统一返回结构、安全边界、
失败分类、验证和审计的 MCP 工具。仓库内 Agent 还实现了小规模、固定角色的最小权限委派，
但不等同于完整复刻 Codex 的跨对话语义记忆、开放式多 Agent 调度和云端执行平台。

## 两种 Agent 模式

- `delegated`：连接支持工具调用的 Provider 后，协调器可以选择知识、文件、Git、CI、命令
  五个专职子 Agent。每个子 Agent 拥有独立上下文和单工具白名单，但当前共用同一 Provider，
  采用顺序委派，不支持并行、子 Agent 再委派、长期计划或动态创建新角色。无 Provider 时仍
  使用固定知识问答链作为离线回退，前端会明确显示它不是动态委派。
- `single`：需要支持 function calling 的真实 Provider。模型能看到 `tools/list` 返回的 5 个
  工具，并在最多 6 轮模型—工具交互中自主选择。代码会阻止未发现工具和非重试失败的相同
  参数原样调用，但没有复杂计划器、跨对话语义记忆或应用层累计 token 上限。

`max_tokens` 是注入给模型的证据或单次工具结果预算。它不等于供应商上下文窗口、输出上限、
累计 token 或费用上限。

## 五个工具的边界

- `search_notes`：使用 Markdown 分块和中文 unigram/bigram 稀疏评分，不是向量数据库，
  对同义词、跨语言和深层语义的召回有限。返回的 `path` 相对 `knowledge_root`。知识子 Agent
  每任务最多查询 3 次并按 `path:line` 去重；这能控制查询膨胀，但可能牺牲复杂问题的召回率。
- `file_access`：只处理 `workspace_root` 内的相对路径和 UTF-8 文本；限制文件大小，不提供
  delete/move，也不能用知识库路径或任意绝对路径绕过 `resolve_inside`。
- `git_query`：只读查询 `repository_root` 下的 status/branch/log/diff，不执行 commit、push、
  checkout 或 reset。
- `ci_status`：只读取状态，不执行 CI。本地模式发现工作流并读取可选的 `.ci/status.json`；
  没有记录时返回 `unknown`。GitHub 模式只读取 Actions runs，不下载完整 job/step 日志。
- `run_command`：本地模式是白名单、参数、cwd、超时和输出受限的进程执行，不是强隔离。
  Python 仅允许工作区内已有 `.py` 文件，Git 仅允许只读子命令；非零退出码是正常观察结果。

## 安全与隔离

`knowledge_root`、`workspace_root`、`repository_root` 和 `runtime_root` 相互分离。扩大其中一个
根目录不会自动扩大其他工具的权限。

本地 `run_command` 仍继承启动 MCP 服务的操作系统用户权限，工作区脚本也可能主动访问主机
其他资源。因此只能称“受限执行”。`MCP_SANDBOX=docker` 使用无网络、非 root、只读容器根和
资源限制，隔离更强，但仍把 workspace 以可写方式挂载，并依赖本机 Docker。

## Verification Loop 与工具审计

Verification Loop 验证工具后置条件，例如检索证据能否回到原文、写入后哈希是否一致、Git
输出是否可解析、CI 响应结构是否合法。它不会自动证明高层业务目标已经完成，也不会自动修复
所有失败。

SQLite 审计记录经过 MCP 注册层的工具调用、调用方、耗时、错误分类和 attempt。`content`、
`new_text`、Key、Token 等字段会脱敏，长字符串会截断。它不是操作系统级审计，也不会记录
绕过 MCP 后直接在外部终端执行的操作。

知识回答的 `citation_coverage` 只检查每个实质性文本单元是否带有本轮允许的 `path:line`，或
是否明确声明“无证据”。它可以发现漏引和伪造路径，但不能证明引文与结论之间的语义蕴含关系；
最终事实准确性仍需评测或人工复核。

## Provider、评测与数据

- Web 保存 API Key 当前仅支持 Windows DPAPI；非 Windows 平台使用环境变量或单次内存配置。
- 本地无 Key 总结器是确定性测试替身，不代表真实大模型质量。
- 配置 Provider 后，评测器会真实运行 single/delegated 对应实现；无 Provider 时 single 明确
  拒绝运行，delegated 只做确定性离线回归。修复前生成的历史报告不能比较两种编排模式。
- `reports/*.json` 默认不提交。只有能追溯到真实 provider、model、时间和任务集的报告，才能
  作为模型效果证据。
- 会话记忆明文保存在本机、已被 Git 忽略的 `.runtime/traces.sqlite3`，最多 50 条/会话；清空
  按钮只删除当前 conversation_id 的消息。项目没有用户登录和多租户隔离，因此仅适合本机使用。

## 当前非目标

项目当前不包含生产级身份认证、租户隔离、分布式任务队列、向量数据库、跨对话语义长期记忆、完整
GitHub Actions 完整日志分析、并行/递归/动态多 Agent 调度，以及对任意不可信代码的绝对安全执行。
