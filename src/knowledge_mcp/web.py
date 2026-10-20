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

import json
import os
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from .agent import AgentTrace, KnowledgeAgent, ProviderSummaryAgent, SummaryAgent
from .config import Settings
from .context import ContextBudgetManager, MetricsRecorder
from .providers import ProviderClient, ProviderConfig, ProviderError, provider_catalog
from .security import SecurityViolation, resolve_inside
from .services import TEXT_SUFFIXES

STRATEGIES = ("full", "relevant", "summary")
DEFAULT_BUDGET = 800
MIN_BUDGET, MAX_BUDGET = 64, 8000
MAX_QUERY_CHARS = 500

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


def _parse_provider(body: dict[str, Any]) -> ProviderConfig | JSONResponse | None:
    try:
        return ProviderConfig.from_payload(body.get("provider"))
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

    async def run_strategies(
        query: str,
        budget: int,
        strategies: tuple[str, ...],
        provider: ProviderConfig | None,
    ) -> list[dict]:
        traces: list[dict[str, Any]] = []
        # In-memory MCP client: same JSON-RPC protocol as /mcp, without a network hop.
        async with Client(server) as client:
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
                trace = await agent.answer_with_trace(query, strategy)  # type: ignore[arg-type]
                traces.append(_trace_to_dict(trace))
        return traces

    @server.custom_route("/api/providers", ["GET"])
    async def providers(_: Request) -> JSONResponse:
        return _ok({"providers": provider_catalog(), "secret_storage": "memory-only"})

    @server.custom_route("/api/provider/test", ["POST"])
    async def provider_test(request: Request) -> JSONResponse:
        parsed = _parse_provider(await _body(request))
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
        provider = _parse_provider(body)
        if isinstance(provider, JSONResponse):
            return provider
        strategy = str(body.get("strategy") or "relevant")
        if strategy not in STRATEGIES:
            return _fail("invalid_strategy", "strategy 必须是 full / relevant / summary")
        try:
            [trace] = await run_strategies(*parsed, (strategy,), provider)
        except ProviderError as exc:
            return _fail("provider_failed", str(exc), 502)
        except RuntimeError as exc:  # search_notes returned ok=false
            return _fail("agent_failed", str(exc), 422)
        return _ok(trace)

    @server.custom_route("/api/agent/compare", ["POST"])
    async def agent_compare(request: Request) -> JSONResponse:
        body = await _body(request)
        parsed = _parse_query_and_budget(body)
        if isinstance(parsed, JSONResponse):
            return parsed
        provider = _parse_provider(body)
        if isinstance(provider, JSONResponse):
            return provider
        try:
            traces = await run_strategies(*parsed, STRATEGIES, provider)
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
