from __future__ import annotations

from pathlib import Path

from knowledge_mcp.context import (
    ContextBudgetManager,
    MetricsRecorder,
    estimate_tokens,
    pack_conversation_history,
)


def test_relevant_context_stays_inside_budget() -> None:
    manager = ContextBudgetManager(max_tokens=64)
    pack = manager.pack(
        "MCP tools",
        ["unrelated " * 100, "MCP tools connect agents to data " * 20],
        strategy="relevant",
    )

    assert pack.estimated_tokens <= 64
    assert "MCP" in pack.text
    assert pack.truncated


def test_metrics_compare_strategies(tmp_path: Path) -> None:
    recorder = MetricsRecorder(tmp_path / "metrics.jsonl")
    pack = ContextBudgetManager(64).pack("MCP", ["MCP evidence"], "relevant")
    recorder.record("relevant", pack, "answer", True)

    summary = recorder.summarize()

    assert summary["runs"] == 1
    assert summary["by_strategy"]["relevant"]["success_rate"] == 1
    assert estimate_tokens("abcd") == 1


def test_context_strategies_use_distinct_sources() -> None:
    manager = ContextBudgetManager(200)
    chunks = [
        "a.md:2 | relevant evidence\n" + "\n".join(f"details line {index}" for index in range(100)),
        "b.md:3 | unrelated\n" + "\n".join(f"noise line {index}" for index in range(100)),
    ]
    documents = ["a.md:1 | complete document " + "full " * 100]
    full = manager.pack("relevant evidence", chunks, "full", full_documents=documents)
    relevant = manager.pack("relevant evidence", chunks, "relevant", full_documents=documents)
    summary = manager.pack("relevant evidence", chunks, "summary", full_documents=documents)
    assert "complete document" in full.text
    assert "relevant evidence" in relevant.text
    assert summary.summarized is True
    assert len({full.text, relevant.text, summary.text}) == 3


def test_conversation_memory_keeps_newest_messages_inside_budget() -> None:
    messages = [
        {"role": "user", "content": "old " * 100},
        {"role": "assistant", "content": "older answer " * 80},
        {"role": "user", "content": "最近的问题"},
        {"role": "assistant", "content": "最近的回答"},
    ]

    packed = pack_conversation_history(messages, 64)

    assert packed.estimated_tokens <= 64
    assert packed.messages[-1]["content"] == "最近的回答"
    assert packed.truncated is True
