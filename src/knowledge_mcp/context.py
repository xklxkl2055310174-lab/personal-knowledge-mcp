"""Context packing strategies and lightweight token-cost metrics."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Strategy = Literal["full", "relevant", "summary"]


def estimate_tokens(text: str) -> int:
    """Portable approximation used for budgets; providers may replace it with a tokenizer."""

    ascii_count = len(text.encode("ascii", errors="ignore"))
    non_ascii_count = len(text) - ascii_count
    return max(1, ascii_count // 4 + non_ascii_count)


@dataclass(slots=True)
class ContextPack:
    text: str
    strategy: Strategy
    estimated_tokens: int
    original_tokens: int
    truncated: bool


class ContextBudgetManager:
    def __init__(self, max_tokens: int = 1_200) -> None:
        if max_tokens < 64:
            raise ValueError("max_tokens must be at least 64")
        self.max_tokens = max_tokens

    def pack(self, query: str, chunks: list[str], strategy: Strategy = "relevant") -> ContextPack:
        original = "\n\n".join(chunks)
        original_tokens = estimate_tokens(original)
        if strategy == "full" and original_tokens <= self.max_tokens:
            return ContextPack(original, strategy, original_tokens, original_tokens, False)

        if strategy == "relevant":
            tokens = {part.casefold() for part in re.findall(r"[\w\u4e00-\u9fff]+", query)}
            ranked = sorted(
                chunks,
                key=lambda chunk: sum(chunk.casefold().count(token) for token in tokens),
                reverse=True,
            )
            candidate = self._fit(ranked)
        elif strategy == "summary":
            lines = [line.strip() for line in original.splitlines() if line.strip()]
            candidate = self._fit([f"- {line[:220]}" for line in lines[:20]])
        else:
            candidate = self._fit(chunks)

        used = estimate_tokens(candidate)
        return ContextPack(candidate, strategy, used, original_tokens, used < original_tokens)

    def _fit(self, chunks: list[str]) -> str:
        selected: list[str] = []
        for chunk in chunks:
            proposed = "\n\n".join([*selected, chunk])
            if estimate_tokens(proposed) > self.max_tokens:
                remaining_chars = max(
                    0, (self.max_tokens - estimate_tokens("\n\n".join(selected))) * 4
                )
                if remaining_chars >= 40:
                    selected.append(chunk[:remaining_chars].rstrip() + "…")
                break
            selected.append(chunk)
        return "\n\n".join(selected)


@dataclass(slots=True)
class RunMetric:
    strategy: Strategy
    input_tokens: int
    output_tokens: int
    success: bool
    timestamp: str


class MetricsRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, strategy: Strategy, context: ContextPack, output: str, success: bool) -> None:
        metric = RunMetric(
            strategy=strategy,
            input_tokens=context.estimated_tokens,
            output_tokens=estimate_tokens(output),
            success=success,
            timestamp=datetime.now(UTC).isoformat(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(metric), ensure_ascii=False) + "\n")

    def summarize(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"runs": 0, "by_strategy": {}}
        rows = [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line
        ]
        grouped: dict[str, dict[str, float]] = {}
        for row in rows:
            item = grouped.setdefault(
                row["strategy"], {"runs": 0, "successes": 0, "input_tokens": 0, "output_tokens": 0}
            )
            item["runs"] += 1
            item["successes"] += int(row["success"])
            item["input_tokens"] += row["input_tokens"]
            item["output_tokens"] += row["output_tokens"]
        for item in grouped.values():
            item["success_rate"] = item["successes"] / item["runs"]
        return {"runs": len(rows), "by_strategy": grouped}
