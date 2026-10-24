# MCP 工具契约

> 本文由 `scripts/generate_tools_doc.py` 从真实 `tools/list` 结果生成，请勿手改。

统一返回 `{ok, data, error, meta}`；`meta` 至少含 `trace_id/tool/duration_ms/attempts/caller`。

## `search_notes`

Search indexed UTF-8 Markdown/text notes. Returns ranked source chunks with path, heading and exact line range. parameter errors are not retryable; transient index or evidence-verification failures may be retried. Zero hits are a valid success.

- Annotations：`{"readOnlyHint": true, "idempotentHint": true}`
- 失败语义：empty_query/invalid_top_k(parameter); search_verification_failed(transient)
- 自动重试：仅 transient

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `query` | string | 是 | `—` | Natural-language query; non-empty. Example: 上下文预算怎么控制 |
| `top_k` | integer | 否 | `5` | Maximum chunks to return, service-validated range 1-20. Example: 5 |

返回：统一 ToolResult；具体 `data` 字段见工具描述与 API 示例。

## `file_access`

List/read UTF-8 files or atomically write/replace them inside MCP_WORKSPACE_ROOT. Returns hashes and backup_id for writes. permission/parameter/conflict errors must not be retried; transient verification failures may be retried.

- Annotations：`{"destructiveHint": true, "idempotentHint": false, "openWorldHint": false}`
- 失败语义：parameter; path_not_allowed(permission); file_changed/file_exists(conflict); verification_failed(transient)
- 自动重试：仅 transient；写入自带校验/回滚

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `action` | list \| read \| write \| replace | 是 | `—` | Operation: list directory, read file, write whole file, or replace one exact block. |
| `path` | string | 否 | `.` | Relative path inside MCP_WORKSPACE_ROOT; absolute paths and traversal are rejected. Example: examples/demo.md |
| `content` | string / null | 否 | `None` | Complete UTF-8 content required by action=write. |
| `mode` | create \| overwrite | 否 | `overwrite` | write behavior: create rejects an existing file; overwrite replaces it. |
| `expected_sha256` | string / null | 否 | `None` | Optional 64-hex hash from read; mismatch returns non-retryable conflict. |
| `old_text` | string / null | 否 | `None` | For replace: exact non-empty text that must occur once. |
| `new_text` | string / null | 否 | `None` | For replace: UTF-8 replacement text, which may be empty. |

返回：统一 ToolResult；具体 `data` 字段见工具描述与 API 示例。

## `git_query`

Read Git status, current branch, recent log, or diff under MCP_REPOSITORY_ROOT. Returns raw output plus structured fields. parameter/permission failures are not retryable; timeouts and transient Git failures use bounded retries.

- Annotations：`{"readOnlyHint": true, "idempotentHint": true}`
- 失败语义：parameter/permission; operation_timeout(timeout); git_command_failed(transient)
- 自动重试：仅 timeout/transient

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `operation` | status \| branch \| log \| diff | 否 | `status` | Read-only Git operation. Example: status |
| `repo_path` | string | 否 | `.` | Repository path relative to MCP_REPOSITORY_ROOT. Use '.' for root. |
| `limit` | integer | 否 | `10` | Maximum log items; service-validated range 1-50. |

返回：统一 ToolResult；具体 `data` 字段见工具描述与 API 示例。

## `ci_status`

Read local CI metadata or GitHub Actions runs. GitHub 404 is parameter, 401/403 permission, rate-limit/5xx/network errors transient. GitHub outages fall back to local status with stale=true.

- Annotations：`{"readOnlyHint": true, "idempotentHint": true, "openWorldHint": true}`
- 失败语义：parameter/permission; github_rate_limited/github_server_error/github_unreachable(transient)
- 自动重试：仅 timeout/transient；可回退本地状态

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `source` | local \| github | 否 | `local` | CI source: local metadata or GitHub Actions. |
| `repository` | string / null | 否 | `None` | Required for github, owner/name form. Example: octocat/Hello-World |
| `limit` | integer | 否 | `5` | Maximum GitHub runs; service-validated range 1-20. |

返回：统一 ToolResult；具体 `data` 字段见工具描述与 API 示例。

## `run_command`

Run an allowlisted command in the workspace with minimal environment, argument policy, timeout and output limit. Non-zero exit_code is a successful observation. Commands are never auto-retried. MCP_SANDBOX=docker enables strong isolation.

- Annotations：`{"destructiveHint": true, "idempotentHint": false, "openWorldHint": false}`
- 失败语义：parameter/permission; command_timeout(timeout); command_failed_to_start(transient)
- 自动重试：否（命令不假定幂等）

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `command` | array | 是 | `—` | Argument vector without a shell. Python accepts only a workspace .py script; Git accepts read-only subcommands. |
| `cwd` | string | 否 | `.` | Working directory relative to MCP_WORKSPACE_ROOT. Example: . |
| `timeout_seconds` | number / null | 否 | `None` | Positive timeout capped at 30 seconds; omit for server default. |

返回：统一 ToolResult；具体 `data` 字段见工具描述与 API 示例。
