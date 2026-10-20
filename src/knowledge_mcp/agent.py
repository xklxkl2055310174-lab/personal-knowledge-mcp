"""Knowledge Agent orchestration with local and real-LLM summary modes.

The retrieval child Agent always gets evidence through MCP.  The summary child Agent can use
the deterministic local implementation or a configured cloud/local LLM provider without
changing the MCP boundary or context-budget logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .context import ContextBudgetManager, ContextPack, MetricsRecorder, Strategy, estimate_tokens
from .providers import ProviderClient


class ToolClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _structured(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structured_content", None)
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("MCP client did not return structured content")


@dataclass(slots=True)
class SearchAgent:
    client: ToolClient

    async def run(self, query: str, top_k: int = 5) -> list[str]:
        result = _structured(
            await self.client.call_tool("search_notes", {"query": query, "top_k": top_k})
        )
        if not result.get("ok"):
            message = (result.get("error") or {}).get("message", "search failed")
            raise RuntimeError(message)
        return [
            f"{match['path']}:{match['line']} | {match['snippet']}"
            for match in result["data"]["matches"]
        ]


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
        self, query: str, strategy: Strategy = "relevant", top_k: int = 5
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
        chunks = await SearchAgent(self.client).run(query, top_k)
        steps.append(
            AgentStep(
                "search",
                "检索子 Agent · search_notes",
                _elapsed_ms(started),
                {
                    "tool": "search_notes",
                    "arguments": {"query": query, "top_k": top_k},
                    "hits": len(chunks),
                },
            )
        )

        started = time.perf_counter()
        context = self.context_manager.pack(query, chunks, strategy)
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
        success = bool(chunks)
        self.metrics.record(strategy, context, answer, success=success)
        steps.append(
            AgentStep(
                "writeback", "结果回写 · 指标记录", _elapsed_ms(started), {"success": success}
            )
        )
        return AgentTrace(
            query=query,
            strategy=strategy,
            answer=answer,
            success=success,
            evidence=chunks,
            context=context,
            steps=steps,
            total_ms=_elapsed_ms(begin),
        )
