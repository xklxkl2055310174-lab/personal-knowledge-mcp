"""Knowledge Agent orchestration with local and real-LLM summary modes.

The retrieval child Agent always gets evidence through MCP.  The summary child Agent can use
the deterministic local implementation or a configured cloud/local LLM provider without
changing the MCP boundary or context-budget logic.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from .context import ContextBudgetManager, ContextPack, MetricsRecorder, Strategy, estimate_tokens
from .providers import ProviderClient


class ToolClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...

    async def list_tools(self) -> Any: ...


@dataclass(slots=True)
class RestrictedToolClient:
    client: ToolClient
    allowed: frozenset[str]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.allowed:
            raise PermissionError(f"tool {name} is not delegated to this Agent")
        if name == "file_access" and arguments.get("action") not in {"list", "read"}:
            raise PermissionError("retrieval Agent only has file_access list/read")
        return await self.client.call_tool(name, arguments)

    async def list_tools(self) -> Any:
        result = await self.client.list_tools()
        result.tools = [tool for tool in result.tools if tool.name in self.allowed]
        return result


def _structured(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structured_content", None)
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("MCP client did not return structured content")


@dataclass(slots=True)
class SearchResult:
    evidence: list[str]
    chunks: list[str]
    documents: list[str]
    meta: dict[str, Any]


@dataclass(slots=True)
class SearchAgent:
    client: ToolClient

    async def run(self, query: str, top_k: int = 5) -> SearchResult:
        result = _structured(
            await self.client.call_tool("search_notes", {"query": query, "top_k": top_k})
        )
        if not result.get("ok"):
            message = (result.get("error") or {}).get("message", "search failed")
            raise RuntimeError(message)
        matches = result["data"]["matches"]
        first_line_by_path = {match["path"]: match["line"] for match in reversed(matches)}
        return SearchResult(
            evidence=[f"{match['path']}:{match['line']} | {match['snippet']}" for match in matches],
            chunks=[
                (
                    f"{match['path']}:{match['line']} | [{match['heading']}; "
                    f"lines {match['start_line']}-{match['end_line']}]\n{match['content']}"
                )
                for match in matches
            ],
            documents=[
                (
                    f"{document['path']}:{first_line_by_path.get(document['path'], 1)} |\n"
                    f"{document['content']}"
                )
                for document in result["data"].get("documents", [])
            ],
            meta=result.get("meta") or {},
        )


@dataclass(slots=True)
class SummaryOutput:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class AnswerAgent(Protocol):
    async def run(self, query: str, context: str) -> SummaryOutput: ...


class SummaryAgent:
    async def run(self, query: str, context: str) -> SummaryOutput:
        if not context.strip():
            return SummaryOutput(
                f"没有在知识库中找到与“{query}”相关的内容。",
                detail={"mode": "local", "provider": "local"},
            )
        lines = [line.strip("- ") for line in context.splitlines() if line.strip()]
        evidence = "\n".join(f"- {line}" for line in lines[:8])
        text = f"问题：{query}\n\n基于知识库证据：\n{evidence}"
        return SummaryOutput(
            text,
            output_tokens=estimate_tokens(text),
            detail={"mode": "local", "provider": "local"},
        )


@dataclass(slots=True)
class ProviderSummaryAgent:
    client: ProviderClient

    async def run(self, query: str, context: str) -> SummaryOutput:
        completion = await self.client.answer(query, context)
        return SummaryOutput(
            completion.text,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            detail={
                "mode": "llm",
                **self.client.config.public(),
                "provider_input_tokens": completion.input_tokens,
                "provider_output_tokens": completion.output_tokens,
            },
        )


@dataclass(slots=True)
class ToolCallingAgent:
    """Single model-driven Agent; all execution still crosses the MCP Client boundary."""

    client: ToolClient
    provider: ProviderClient
    context_manager: ContextBudgetManager
    max_steps: int = 6
    max_total_tokens: int = 8_000

    async def run(self, query: str) -> SummaryOutput:
        discovered = (await self.client.list_tools()).tools
        tools = [
            {
                "type": "function",
                "function": {
                    "name": item.name,
                    "description": item.description or "",
                    "parameters": item.input_schema,
                },
            }
            for item in discovered
        ]
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是 Coding Agent。根据任务自主选择 MCP 工具；引用知识时必须保留"
                    "工具返回的 path:line；参数/权限错误不要重试。"
                ),
            },
            {"role": "user", "content": query},
        ]
        total_input = 0
        total_output = 0
        tool_calls = 0
        for step in range(1, self.max_steps + 1):
            turn = await self.provider.chat(messages, tools)
            total_input += turn.input_tokens or estimate_tokens(
                json.dumps(messages, ensure_ascii=False)
            )
            total_output += turn.output_tokens or estimate_tokens(turn.text)
            if total_input + total_output > self.max_total_tokens:
                raise RuntimeError("Agent total token budget exceeded")
            if not turn.tool_calls:
                if not turn.text.strip():
                    raise RuntimeError("Model returned neither text nor tool calls")
                return SummaryOutput(
                    turn.text,
                    total_input,
                    total_output,
                    {
                        "mode": "single",
                        **self.provider.config.public(),
                        "tool_calls": tool_calls,
                        "steps": step,
                    },
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": turn.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in turn.tool_calls
                    ],
                }
            )
            for call in turn.tool_calls:
                result = _structured(await self.client.call_tool(call.name, call.arguments))
                raw = json.dumps(result, ensure_ascii=False)
                packed = self.context_manager.pack(
                    query,
                    [raw],
                    "summary" if estimate_tokens(raw) > 500 else "relevant",
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": packed.text,
                    }
                )
                tool_calls += 1
        raise RuntimeError("Agent exceeded maximum tool-calling steps")


@dataclass(slots=True)
class AgentStep:
    """One stage of the task-intake -> retrieval -> context -> summary -> write-back chain."""

    name: str
    title: str
    duration_ms: int
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTrace:
    query: str
    strategy: Strategy
    answer: str
    success: bool
    evidence: list[str]
    context: ContextPack
    steps: list[AgentStep]
    total_ms: int
    mode: str = "delegated"
    citations_valid: bool = False
    output_path: str | None = None


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@dataclass(slots=True)
class KnowledgeAgent:
    client: ToolClient
    context_manager: ContextBudgetManager
    metrics: MetricsRecorder
    summary_agent: AnswerAgent = field(default_factory=SummaryAgent)

    async def answer(self, query: str, strategy: Strategy = "relevant") -> str:
        return (await self.answer_with_trace(query, strategy)).answer

    async def answer_with_trace(
        self,
        query: str,
        strategy: Strategy = "relevant",
        top_k: int = 5,
        mode: str = "delegated",
    ) -> AgentTrace:
        """Run the full chain and keep a per-stage trace for observability (Web console)."""

        begin = time.perf_counter()
        steps = [
            AgentStep(
                "intake",
                "任务接入",
                0,
                {"query": query, "strategy": strategy, "budget": self.context_manager.max_tokens},
            )
        ]

        # Delegation 1: retrieval agent collects evidence through MCP.
        started = time.perf_counter()
        retrieval_client = RestrictedToolClient(
            self.client,
            frozenset({"search_notes", "file_access", "git_query"}),
        )
        retrieval = await SearchAgent(retrieval_client).run(query, top_k)
        steps.append(
            AgentStep(
                "search",
                "检索子 Agent · search_notes",
                _elapsed_ms(started),
                {
                    "tool": "search_notes",
                    "arguments": {"query": query, "top_k": top_k},
                    "hits": len(retrieval.evidence),
                    "attempts": retrieval.meta.get("attempts", []),
                    "fallback": retrieval.meta.get("fallback"),
                },
            )
        )

        started = time.perf_counter()
        context = self.context_manager.pack(
            query,
            retrieval.chunks,
            strategy,
            full_documents=retrieval.documents,
        )
        if (
            strategy == "summary"
            and isinstance(self.summary_agent, ProviderSummaryAgent)
            and context.truncated
        ):
            compressed = await self.summary_agent.run(
                "压缩以下证据，必须保留每条来源的 path:line，不回答原问题。",
                context.text,
            )
            context = ContextPack(
                compressed.text,
                strategy,
                compressed.output_tokens or estimate_tokens(compressed.text),
                context.original_tokens,
                True,
                ("provider_usage" if compressed.output_tokens is not None else "estimate"),
                True,
            )
        steps.append(
            AgentStep(
                "context",
                "上下文管理",
                _elapsed_ms(started),
                {
                    "strategy": strategy,
                    "original_tokens": context.original_tokens,
                    "estimated_tokens": context.estimated_tokens,
                    "truncated": context.truncated,
                    "token_source": context.token_source,
                    "summarized": context.summarized,
                },
            )
        )

        # Delegation 2: summary agent produces an evidence-bound answer.
        started = time.perf_counter()
        summary = await self.summary_agent.run(query, context.text)
        answer = summary.text
        steps.append(
            AgentStep(
                "summary",
                "LLM 总结子 Agent" if summary.detail.get("mode") == "llm" else "总结子 Agent",
                _elapsed_ms(started),
                {
                    "input_tokens": summary.input_tokens,
                    "output_tokens": summary.output_tokens or estimate_tokens(answer),
                    **summary.detail,
                },
            )
        )

        started = time.perf_counter()
        allowed_citations = {item.split(" | ", 1)[0] for item in retrieval.evidence}
        answer_citations = set(re.findall(r"([\w./-]+\.\w+:\d+)", answer))
        citations_valid = bool(allowed_citations) and bool(answer_citations)
        citations_valid = citations_valid and answer_citations <= allowed_citations
        success = bool(retrieval.evidence) and citations_valid
        self.metrics.record(
            strategy,
            context,
            answer,
            success=success,
            mode=mode,
            input_tokens=summary.input_tokens,
            output_tokens=summary.output_tokens,
        )
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", query).strip("-")[:32] or "task"
        output_path = (
            f"outputs/{datetime.now(UTC).date().isoformat()}-{slug}-{uuid.uuid4().hex[:8]}.md"
        )
        document = (
            f"# Agent 任务结果\n\n"
            f"- 问题：{query}\n- 模式：{mode}\n- 策略：{strategy}\n"
            f"- 输入 token：{summary.input_tokens or context.estimated_tokens}\n"
            f"- 输出 token：{summary.output_tokens or estimate_tokens(answer)}\n"
            f"- 引用校验：{'通过' if citations_valid else '未通过'}\n\n"
            f"## 回答\n\n{answer}\n\n## 证据\n\n"
            + "\n".join(f"- {item}" for item in retrieval.evidence)
            + "\n"
        )
        write_result = _structured(
            await self.client.call_tool(
                "file_access",
                {
                    "action": "write",
                    "path": output_path,
                    "content": document,
                    "mode": "create",
                },
            )
        )
        if not write_result.get("ok"):
            output_path = None
        steps.append(
            AgentStep(
                "writeback",
                "结果回写 · MCP file_access",
                _elapsed_ms(started),
                {
                    "success": success,
                    "path": output_path,
                    "citations_valid": citations_valid,
                },
            )
        )
        return AgentTrace(
            query=query,
            strategy=strategy,
            answer=answer,
            success=success,
            evidence=retrieval.evidence,
            context=context,
            steps=steps,
            total_ms=_elapsed_ms(begin),
            mode=mode,
            citations_valid=citations_valid,
            output_path=output_path,
        )
