from __future__ import annotations

from pathlib import Path

from knowledge_mcp.context import ContextBudgetManager, MetricsRecorder, estimate_tokens


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
