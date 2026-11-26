"""HTTP endpoints for the Web console.

These routes are *not* part of the MCP protocol surface.  The browser talks to the five tools
through the real ``/mcp`` endpoint; the routes here only cover things MCP does not model:

- ``/api/overview``        service configuration and metric summary
- ``/api/agent/ask``       run the two-child-agent chain and return a per-stage trace
- ``/api/agent/compare``   run all three context strategies on one question
- ``/api/agent/metrics``   aggregated token cost / success rate per strategy
- ``/api/providers``       supported LLM provider presets (never secrets)
- ``/api/provider/test``   verify one ephemeral provider configuration
- ``/api/notes``           read-only view of the knowledge base
- ``/`` and ``/{path}``    the built Vue single-page app (``web/dist``)

The Agent still reaches tools through an MCP ``Client`` (in-memory transport), so the
"Agent only orchestrates, MCP executes" boundary is preserved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

from .agent import (
    AgentTrace,
    DelegatedAgent,
    KnowledgeAgent,
    ProviderSummaryAgent,
    SummaryAgent,
    ToolCallingAgent,
)
from .config import Settings
from .context import ContextBudgetManager, MetricsRecorder, pack_conversation_history
from .index import TEXT_SUFFIXES, NoteIndex
from .provider_store import ProviderStore
from .providers import ProviderClient, ProviderConfig, ProviderError, provider_catalog
from .security import SecurityViolation, resolve_inside
from .telemetry import TraceStore, trace_scope

logger = logging.getLogger(__name__)

STRATEGIES = ("full", "relevant", "summary")
DEFAULT_BUDGET = 800
MIN_BUDGET, MAX_BUDGET = 64, 8000
MAX_QUERY_CHARS = 500
MAX_MEMORY_TOKENS = 1_200
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_NOT_BUILT_HTML = """<!doctype html><meta charset="utf-8"><title>Personal Knowledge MCP</title>
<body style="font-family:system-ui;max-width:640px;margin:80px auto;line-height:1.7">
<h2>前端尚未构建</h2>
<p>开发模式：<code>cd web &amp;&amp; npm install &amp;&amp; npm run dev</code>，
然后访问 <a href="http://localhost:5173">http://localhost:5173</a>。</p>
<p>生产模式：<code>cd web &amp;&amp; npm run build</code> 后刷新本页。</p>
<p>MCP 端点：<code>/mcp</code> · 健康检查：<a href="/health">/health</a></p></body>"""


def _ok(data: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data, "error": None})


def _fail(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "data": None, "error": {"code": code, "message": message}},
        status_code=status,
    )


def _nested_exception(
    error: BaseException, expected: type[Exception]
) -> Exception | None:
    if isinstance(error, expected):
        return error
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            match = _nested_exception(nested, expected)
            if match is not None:
                return match
    return None


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_query_and_budget(body: dict[str, Any]) -> tuple[str, int] | JSONResponse:
    query = str(body.get("query") or "").strip()
    if not query:
        return _fail("empty_query", "query 不能为空")
    if len(query) > MAX_QUERY_CHARS:
        return _fail("query_too_long", f"query 不能超过 {MAX_QUERY_CHARS} 个字符")
    try:
        budget = int(body.get("max_tokens", DEFAULT_BUDGET))
    except (TypeError, ValueError):
        return _fail("invalid_budget", "max_tokens 必须是整数")
    if not MIN_BUDGET <= budget <= MAX_BUDGET:
        return _fail("invalid_budget", f"max_tokens 必须在 {MIN_BUDGET}-{MAX_BUDGET} 之间")
    return query, budget


def _parse_conversation_id(body: dict[str, Any]) -> str | JSONResponse:
    value = str(body.get("conversation_id") or uuid.uuid4().hex).strip()
    if not _CONVERSATION_ID.fullmatch(value):
        return _fail(
            "invalid_conversation_id",
            "conversation_id 只能包含字母、数字、下划线和连字符，长度为 8-64",
        )
    return value


def _parse_provider(
    body: dict[str, Any], store: ProviderStore | None = None
) -> ProviderConfig | JSONResponse | None:
    try:
        if "provider" not in body:
            return ProviderConfig.from_environment() or (store.load() if store else None)
        payload = body.get("provider")
        if isinstance(payload, dict) and payload.get("use_saved") is True:
            saved = store.load() if store else None
            if saved is None:
                raise ProviderError("没有已保存的模型配置，请重新输入 API Key")
            return saved
        return ProviderConfig.from_payload(payload)
    except ProviderError as exc:
        return _fail("invalid_provider", str(exc))


def _trace_to_dict(trace: AgentTrace) -> dict[str, Any]:
    data = asdict(trace)
    original = trace.context.original_tokens
    data["context"]["saved_ratio"] = (
        round(1 - trace.context.estimated_tokens / original, 4) if original else 0.0
    )
    return data


def register_web_routes(server: MCPServer, settings: Settings) -> None:
    metrics = MetricsRecorder(settings.runtime_root / "agent_metrics.jsonl")
    notes_root = settings.knowledge_root / "notes"
    dist_root = Path(
        os.getenv("MCP_WEB_DIST", str(settings.project_root / "web" / "dist"))
    ).resolve()
    traces_store = TraceStore(settings.runtime_root / "traces.sqlite3")
    provider_store = ProviderStore(settings.project_root / ".private" / "provider-config.json")
    note_index = NoteIndex(
        notes_root,
        settings.runtime_root / "notes-index.sqlite3",
        settings.max_file_bytes,
    )
    background_tasks: dict[str, asyncio.Task[None]] = {}
    task_events: dict[str, list[dict[str, Any]]] = {}

    def load_memory(
        conversation_id: str, budget: int
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        stored = traces_store.list_conversation_messages(conversation_id, 50)
        packed = pack_conversation_history(stored, min(budget, MAX_MEMORY_TOKENS))
        return packed.messages, {
            "injected_messages": len(packed.messages),
            "stored_messages": len(stored),
            "estimated_tokens": packed.estimated_tokens,
            "original_tokens": packed.original_tokens,
            "max_tokens": packed.max_tokens,
            "truncated": packed.truncated,
        }

    def emit(task_id: str, event: str, **data: Any) -> None:
        task_events.setdefault(task_id, []).append(
            {
                "event": event,
                "timestamp": datetime.now(UTC).isoformat(),
                "data": data,
            }
        )

    async def run_strategies(
        query: str,
        budget: int,
        strategies: tuple[str, ...],
        provider: ProviderConfig | None,
        mode: str = "delegated",
        task_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict]:
        traces: list[dict[str, Any]] = []
        history = history or []
        if mode == "single" and provider is None:
            raise ProviderError("自主工具模式需要连接支持工具调用的真实模型")
        # In-memory MCP client: same JSON-RPC protocol as /mcp, without a network hop.
        async with Client(server) as client:
            # A real provider activates the actual orchestration mode.  Multi-strategy
            # comparison intentionally keeps using the deterministic QA chain because
            # context strategies are not coordinator-routing strategies.
            if provider and len(strategies) == 1 and mode in {"single", "delegated"}:
                started = time.perf_counter()
                model_client = ProviderClient(provider)
                runtime_agent = (
                    ToolCallingAgent(
                        client,
                        model_client,
                        ContextBudgetManager(budget),
                        context_strategy=strategies[0],
                    )
                    if mode == "single"
                    else DelegatedAgent(
                        client,
                        model_client,
                        ContextBudgetManager(budget),
                        context_strategy=strategies[0],
                    )
                )
                with trace_scope("agent", task_id):
                    output = await runtime_agent.run(query, history)
                total_ms = int((time.perf_counter() - started) * 1000)
                evidence = output.detail.get("evidence", [])
                citations_valid = bool(output.detail.get("citations_valid", True))
                if mode == "single":
                    runtime_steps = [
                        {
                            "name": "agent_loop",
                            "title": "单 Agent 工具调用循环",
                            "duration_ms": total_ms,
                            "detail": {
                                **output.detail,
                                "input_tokens": output.input_tokens,
                                "output_tokens": output.output_tokens,
                                "memory_messages": len(history),
                            },
                        }
                    ]
                else:
                    delegation_log = output.detail.get("delegation_log", [])
                    delegated_ms = sum(
                        int(item.get("duration_ms", 0)) for item in delegation_log
                    )
                    runtime_steps = [
                        {
                            "name": "intake",
                            "title": "协调 Agent · 分析与拆解任务",
                            "duration_ms": 0,
                            "detail": {
                                "query": query,
                                "strategy": strategies[0],
                                "budget": budget,
                                "memory_messages": len(history),
                                "available_specialists": [
                                    "知识检索", "文件操作", "Git 查询", "CI 状态", "命令执行"
                                ],
                            },
                        },
                        *[
                            {
                                "name": f"delegation_{index}",
                                "title": (
                                    f"委派 {index} · "
                                    f"{item.get('label', item.get('specialist'))}"
                                ),
                                "duration_ms": int(item.get("duration_ms", 0)),
                                "detail": item,
                            }
                            for index, item in enumerate(delegation_log, start=1)
                        ],
                        {
                            "name": "coordination",
                            "title": "协调 Agent · 汇总与结果校验",
                            "duration_ms": max(0, total_ms - delegated_ms),
                            "detail": {
                                key: value
                                for key, value in output.detail.items()
                                if key != "delegation_log"
                            }
                            | {
                                "input_tokens": output.input_tokens,
                                "output_tokens": output.output_tokens,
                            },
                        },
                    ]
                return [
                    {
                        "query": query,
                        "strategy": strategies[0],
                        "mode": mode,
                        "answer": output.text,
                        "success": citations_valid,
                        "evidence": evidence,
                        "context": {
                            "text": "",
                            "strategy": strategies[0],
                            "estimated_tokens": output.input_tokens or 0,
                            "original_tokens": output.input_tokens or 0,
                            "truncated": False,
                            "token_source": (
                                "provider_usage" if output.input_tokens is not None else "estimate"
                            ),
                            "summarized": False,
                            "saved_ratio": 0.0,
                        },
                        "steps": runtime_steps,
                        "total_ms": total_ms,
                        "citations_valid": citations_valid,
                        "output_path": None,
                    }
                ]
            summary_agent = (
                ProviderSummaryAgent(ProviderClient(provider)) if provider else SummaryAgent()
            )
            agent = KnowledgeAgent(
                client,
                ContextBudgetManager(budget),
                metrics,
                summary_agent,
            )
            for strategy in strategies:
                with trace_scope("agent", task_id):
                    trace = await agent.answer_with_trace(
                        query,
                        strategy,
                        mode=mode,  # type: ignore[arg-type]
                        history=history,
                    )
                traces.append(_trace_to_dict(trace))
        return traces

    @server.custom_route("/api/providers", ["GET"])
    async def providers(_: Request) -> JSONResponse:
        try:
            saved_provider = provider_store.public()
        except ProviderError as exc:
            return _fail("saved_provider_invalid", str(exc), 500)
        return _ok(
            {
                "providers": provider_catalog(),
                "secret_storage": "windows-dpapi",
                "server_provider_configured": (
                    ProviderConfig.from_environment() is not None or saved_provider is not None
                ),
                "saved_provider": saved_provider,
            }
        )

    @server.custom_route("/api/provider/save", ["POST"])
    async def provider_save(request: Request) -> JSONResponse:
        try:
            config = ProviderConfig.from_payload((await _body(request)).get("provider"))
            if config is None:
                return _fail("invalid_provider", "本地演示模式不需要保存 API Key")
            saved = provider_store.save(config)
        except ProviderError as exc:
            return _fail("provider_save_failed", str(exc))
        except OSError:
            logger.exception("Failed to persist provider configuration")
            return _fail("provider_save_failed", "无法写入本机私有配置目录", 500)
        return _ok({"saved": True, "provider": saved})

    @server.custom_route("/api/provider/saved", ["DELETE"])
    async def provider_delete(_: Request) -> JSONResponse:
        try:
            provider_store.delete()
        except OSError:
            logger.exception("Failed to delete provider configuration")
            return _fail("provider_delete_failed", "无法删除本机模型配置", 500)
        return _ok({"deleted": True})

    @server.custom_route("/api/provider/test", ["POST"])
    async def provider_test(request: Request) -> JSONResponse:
        parsed = _parse_provider(await _body(request), provider_store)
        if isinstance(parsed, JSONResponse):
            return parsed
        if parsed is None:
            return _ok(
                {
                    "connected": True,
                    "provider": {"id": "local", "model": "本地演示总结器"},
                    "preview": "本地模式无需 API 连接。",
                }
            )
        started = time.perf_counter()
        try:
            result = await ProviderClient(parsed).answer(
                "这是连接测试，请用中文简短确认。",
                "connection-test.md:1 | MCP 模型供应商连接测试。",
            )
        except ProviderError as exc:
            return _fail("provider_test_failed", str(exc), 502)
        return _ok(
            {
                "connected": True,
                "provider": parsed.public(),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "preview": result.text[:240],
            }
        )

    @server.custom_route("/api/overview", ["GET"])
    async def overview(_: Request) -> JSONResponse:
        notes = [p for p in notes_root.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES]
        return _ok(
            {
                "service": "personal-knowledge-mcp",
                "version": "0.1.0",
                "mcp_endpoint": "/mcp",
                "config": {
                    "knowledge_root": str(settings.knowledge_root),
                    "workspace_root": str(settings.workspace_root),
                    "repository_root": str(settings.repository_root),
                    "allowed_commands": list(settings.allowed_commands),
                    "command_timeout_seconds": settings.command_timeout_seconds,
                    "max_file_bytes": settings.max_file_bytes,
                    "github_token_configured": bool(settings.github_token),
                },
                "notes_count": len(notes),
                "metrics": metrics.summarize(),
            }
        )

    @server.custom_route("/api/agent/ask", ["POST"])
    async def agent_ask(request: Request) -> JSONResponse:
        body = await _body(request)
        parsed = _parse_query_and_budget(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        conversation_id = _parse_conversation_id(body)
        if isinstance(conversation_id, JSONResponse):
            return conversation_id
        history, memory_meta = load_memory(conversation_id, parsed[1])
        provider = _parse_provider(body, provider_store)
        if isinstance(provider, JSONResponse):
            return provider
        strategy = str(body.get("strategy") or "relevant")
        if strategy not in STRATEGIES:
            return _fail("invalid_strategy", "strategy 必须是 full / relevant / summary")
        mode = str(body.get("mode") or "delegated")
        if mode not in {"single", "delegated"}:
            return _fail("invalid_mode", "mode 必须是 single / delegated")
        traces_store.append_conversation_message(
            conversation_id,
            "user",
            parsed[0],
            {"mode": mode, "strategy": strategy},
        )
        try:
            [trace] = await run_strategies(
                *parsed,
                (strategy,),
                provider,
                mode,
                history=history,
            )
        except ProviderError as exc:
            return _fail("provider_failed", str(exc), 502)
        except RuntimeError as exc:  # search_notes returned ok=false
            return _fail("agent_failed", str(exc), 422)
        except Exception as exc:
            provider_error = _nested_exception(exc, ProviderError)
            if provider_error is not None:
                return _fail("provider_failed", str(provider_error), 502)
            agent_error = _nested_exception(exc, RuntimeError)
            if agent_error is not None:
                return _fail("agent_failed", str(agent_error), 422)
            logger.exception("Unhandled Agent request failure")
            return _fail(
                "agent_internal_error",
                "Agent 内部发生未预期错误，请查看 PyCharm 后端控制台中的 traceback",
                500,
            )
        traces_store.append_conversation_message(
            conversation_id,
            "assistant",
            trace["answer"],
            {"mode": mode, "strategy": strategy},
        )
        trace["conversation_id"] = conversation_id
        trace["memory"] = {**memory_meta, "stored_messages": memory_meta["stored_messages"] + 2}
        return _ok(trace)

    @server.custom_route("/api/conversations/{conversation_id}", ["GET", "DELETE"])
    async def conversation(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        if not _CONVERSATION_ID.fullmatch(conversation_id):
            return _fail("invalid_conversation_id", "conversation_id 格式不合法")
        if request.method == "DELETE":
            cleared = traces_store.clear_conversation(conversation_id)
            return _ok({"conversation_id": conversation_id, "cleared": cleared})
        messages = traces_store.list_conversation_messages(conversation_id, 50)
        return _ok(
            {
                "conversation_id": conversation_id,
                "messages": messages,
                "message_count": len(messages),
            }
        )

    @server.custom_route("/api/agent/compare", ["POST"])
    async def agent_compare(request: Request) -> JSONResponse:
        body = await _body(request)
        parsed = _parse_query_and_budget(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        provider = _parse_provider(body, provider_store)
        if isinstance(provider, JSONResponse):
            return provider
        try:
            traces = await run_strategies(*parsed, STRATEGIES, provider, "delegated")
        except ProviderError as exc:
            return _fail("provider_failed", str(exc), 502)
        except RuntimeError as exc:
            return _fail("agent_failed", str(exc), 422)
        rows = [
            {
                "strategy": t["strategy"],
                "original_tokens": t["context"]["original_tokens"],
                "input_tokens": t["context"]["estimated_tokens"],
                "output_tokens": t["steps"][3]["detail"]["output_tokens"],
                "saved_ratio": t["context"]["saved_ratio"],
                "truncated": t["context"]["truncated"],
                "success": t["success"],
                "total_ms": t["total_ms"],
            }
            for t in traces
        ]
        return _ok({"query": parsed[0], "budget": parsed[1], "rows": rows, "traces": traces})

    @server.custom_route("/api/agent/metrics", ["GET"])
    async def agent_metrics(_: Request) -> JSONResponse:
        return _ok(metrics.summarize())

    @server.custom_route("/api/notes", ["GET"])
    async def list_notes(_: Request) -> JSONResponse:
        items = []
        for path in sorted(notes_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            stat = path.stat()
            items.append(
                {
                    "path": path.relative_to(notes_root).as_posix(),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                }
            )
        return _ok({"root": str(notes_root), "notes": items})

    @server.custom_route("/api/notes/content", ["GET"])
    async def note_content(request: Request) -> JSONResponse:
        relative = request.query_params.get("path", "")
        try:
            target = resolve_inside(notes_root, relative, allow_root=False)
        except SecurityViolation as exc:
            return _fail("path_not_allowed", str(exc), 403)
        if not target.is_file() or target.suffix.lower() not in TEXT_SUFFIXES:
            return _fail("note_not_found", "笔记不存在", 404)
        if target.stat().st_size > settings.max_file_bytes:
            return _fail("file_too_large", "笔记超过 MCP_MAX_FILE_BYTES", 413)
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeError:
            return _fail("not_text", "仅支持 UTF-8 文本", 415)
        return _ok({"path": relative, "content": content})

    async def execute_task(
        task_id: str,
        query: str,
        mode: str,
        strategy: str,
        budget: int,
        provider: ProviderConfig | None,
        conversation_id: str,
        history: list[dict[str, str]],
        memory_meta: dict[str, Any],
    ) -> None:
        traces_store.update_task(task_id, status="running")
        emit(task_id, "step_started", step="agent")
        try:
            [trace] = await run_strategies(
                query,
                budget,
                (strategy,),
                provider,
                mode,
                task_id,
                history,
            )
            trace["conversation_id"] = conversation_id
            trace["memory"] = {
                **memory_meta,
                "stored_messages": memory_meta["stored_messages"] + 2,
            }
            for step in trace.get("steps", []):
                traces_store.record_step(
                    task_id,
                    step["name"],
                    step.get("duration_ms", 0),
                    step.get("detail", {}),
                )
                if step["name"] == "search":
                    emit(task_id, "tool_call", tool="search_notes", detail=step["detail"])
                    emit(task_id, "tool_result", tool="search_notes", ok=True)
                    attempts = step["detail"].get("attempts", [])
                    for attempt in attempts[1:]:
                        emit(task_id, "retry", tool="search_notes", attempt=attempt)
                    if step["detail"].get("fallback"):
                        emit(
                            task_id,
                            "fallback",
                            tool="search_notes",
                            fallback=step["detail"]["fallback"],
                        )
                emit(task_id, "step_finished", step=step["name"], detail=step["detail"])
            summary = next(
                (
                    step.get("detail", {})
                    for step in reversed(trace.get("steps", []))
                    if step.get("name") in {"summary", "agent_loop", "coordination"}
                ),
                {},
            )
            if summary.get("input_tokens") is not None:
                emit(
                    task_id,
                    "llm_usage",
                    input_tokens=summary.get("input_tokens"),
                    output_tokens=summary.get("output_tokens"),
                )
            traces_store.update_task(
                task_id,
                status="completed",
                answer=trace["answer"],
                metrics_json=json.dumps(trace, ensure_ascii=False),
            )
            traces_store.append_conversation_message(
                conversation_id,
                "assistant",
                trace["answer"],
                {"mode": mode, "strategy": strategy, "task_id": task_id},
            )
            emit(task_id, "task_finished", status="completed")
        except asyncio.CancelledError:
            traces_store.update_task(task_id, status="cancelled")
            emit(task_id, "task_finished", status="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - boundary converts to safe API error
            error = {"code": "task_failed", "message": str(exc)[:500]}
            traces_store.update_task(
                task_id,
                status="failed",
                error_json=json.dumps(error, ensure_ascii=False),
            )
            emit(task_id, "error", **error)

    @server.custom_route("/api/tasks", ["POST", "GET"])
    async def tasks(request: Request) -> JSONResponse:
        if request.method == "GET":
            try:
                limit = int(request.query_params.get("limit", "20"))
                offset = int(request.query_params.get("offset", "0"))
            except ValueError:
                return _fail("invalid_pagination", "limit/offset 必须是整数")
            return _ok(
                {
                    "items": traces_store.list_tasks(limit, offset),
                    "limit": limit,
                    "offset": offset,
                }
            )
        body = await _body(request)
        parsed = _parse_query_and_budget(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        provider = _parse_provider(body, provider_store)
        if isinstance(provider, JSONResponse):
            return provider
        mode = str(body.get("mode") or "delegated")
        strategy = str(body.get("strategy") or "relevant")
        if mode not in {"single", "delegated"}:
            return _fail("invalid_mode", "mode 必须是 single / delegated")
        if strategy not in STRATEGIES:
            return _fail("invalid_strategy", "strategy 必须是 full / relevant / summary")
        conversation_id = _parse_conversation_id(body)
        if isinstance(conversation_id, JSONResponse):
            return conversation_id
        history, memory_meta = load_memory(conversation_id, parsed[1])
        traces_store.append_conversation_message(
            conversation_id,
            "user",
            parsed[0],
            {"mode": mode, "strategy": strategy},
        )
        task_id = traces_store.create_task(parsed[0], mode, strategy, conversation_id)
        emit(task_id, "step_started", step="queued")
        background_tasks[task_id] = asyncio.create_task(
            execute_task(
                task_id,
                parsed[0],
                mode,
                strategy,
                parsed[1],
                provider,
                conversation_id,
                history,
                memory_meta,
            )
        )
        return _ok(
            {"task_id": task_id, "status": "queued", "conversation_id": conversation_id}
        )

    @server.custom_route("/api/tasks/{task_id}", ["GET"])
    async def task_detail(request: Request) -> JSONResponse:
        task = traces_store.get_task(request.path_params["task_id"])
        return _ok(task) if task else _fail("task_not_found", "任务不存在", 404)

    @server.custom_route("/api/tasks/{task_id}/cancel", ["POST"])
    async def task_cancel(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        task = traces_store.get_task(task_id)
        if not task:
            return _fail("task_not_found", "任务不存在", 404)
        traces_store.update_task(task_id, cancel_requested=1)
        running = background_tasks.get(task_id)
        if running and not running.done():
            running.cancel()
        return _ok({"task_id": task_id, "status": "cancelling"})

    @server.custom_route("/api/tasks/{task_id}/events", ["GET"])
    async def task_event_stream(request: Request) -> StreamingResponse:
        task_id = request.path_params["task_id"]

        async def stream() -> Any:
            cursor = 0
            while True:
                events = task_events.get(task_id, [])
                while cursor < len(events):
                    item = events[cursor]
                    cursor += 1
                    yield (
                        f"event: {item['event']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    )
                    if item["event"] in {"task_finished", "error"}:
                        return
                task = traces_store.get_task(task_id)
                if not task or task["status"] in {"completed", "failed", "cancelled"}:
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @server.custom_route("/api/tool-calls", ["GET"])
    async def tool_calls(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            limit = int(params.get("limit", "100"))
            offset = int(params.get("offset", "0"))
        except ValueError:
            return _fail("invalid_pagination", "limit/offset 必须是整数")
        items = traces_store.list_tool_calls(
            tool=params.get("tool"),
            category=params.get("category"),
            caller=params.get("caller"),
            since=params.get("since"),
            limit=limit,
            offset=offset,
        )
        return _ok({"items": items, "limit": limit, "offset": offset})

    @server.custom_route("/api/metrics/failures", ["GET"])
    async def failure_metrics(request: Request) -> JSONResponse:
        try:
            hours = int(request.query_params.get("hours", str(24 * 30)))
        except ValueError:
            return _fail("invalid_hours", "hours 必须是整数")
        return _ok({"hours": hours, "rows": traces_store.failure_distribution(hours)})

    @server.custom_route("/api/metrics/strategies", ["GET"])
    async def strategy_metrics(_: Request) -> JSONResponse:
        return _ok(metrics.summarize())

    @server.custom_route("/api/metrics/delegation", ["GET"])
    async def delegation_metrics(_: Request) -> JSONResponse:
        rows = traces_store.list_tasks(100, 0)
        grouped: dict[str, dict[str, int]] = {}
        for row in rows:
            item = grouped.setdefault(row["mode"], {"runs": 0, "completed": 0})
            item["runs"] += 1
            item["completed"] += int(row["status"] == "completed")
        return _ok({"by_mode": grouped})

    @server.custom_route("/api/notes/index", ["GET"])
    async def index_status(_: Request) -> JSONResponse:
        return _ok(note_index.status())

    @server.custom_route("/api/notes/reindex", ["POST"])
    async def reindex(_: Request) -> JSONResponse:
        return _ok({**note_index.sync(), **note_index.status()})

    @server.custom_route("/api/eval/reports", ["GET"])
    async def eval_reports(_: Request) -> JSONResponse:
        report_root = settings.project_root / "reports"
        items = (
            [
                {"id": path.stem, "path": path.name, "size": path.stat().st_size}
                for path in sorted(report_root.glob("*.json"), reverse=True)
            ]
            if report_root.exists()
            else []
        )
        return _ok({"items": items})

    @server.custom_route("/api/eval/reports/{report_id}", ["GET"])
    async def eval_report(request: Request) -> JSONResponse:
        report_id = request.path_params["report_id"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", report_id):
            return _fail("invalid_report_id", "报告 ID 非法")
        target = settings.project_root / "reports" / f"{report_id}.json"
        if not target.is_file():
            return _fail("report_not_found", "报告不存在", 404)
        return _ok(json.loads(target.read_text(encoding="utf-8")))

    @server.custom_route("/{path:path}", ["GET"], include_in_schema=False)
    async def spa(request: Request) -> Response:
        relative = request.path_params.get("path", "")
        if relative.startswith("api/"):
            return _fail("not_found", "接口不存在", 404)
        index = dist_root / "index.html"
        if not index.is_file():
            return HTMLResponse(_NOT_BUILT_HTML)
        if relative:
            try:
                asset = resolve_inside(dist_root, relative)
            except SecurityViolation:
                return _fail("not_found", "资源不存在", 404)
            if asset.is_file():
                return FileResponse(asset)
        return FileResponse(index)  # SPA history-mode fallback
