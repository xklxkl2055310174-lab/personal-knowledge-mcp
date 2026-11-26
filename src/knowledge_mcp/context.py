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
    token_source: str = "estimate"
    summarized: bool = False


@dataclass(slots=True)
class ConversationContext:
    messages: list[dict[str, str]]
    estimated_tokens: int
    original_tokens: int
    truncated: bool
    max_tokens: int


def pack_conversation_history(
    messages: list[dict[str, Any]], max_tokens: int
) -> ConversationContext:
    """Keep the newest valid user/assistant messages within a separate memory budget."""

    if max_tokens < 1:
        return ConversationContext([], 0, 0, bool(messages), max_tokens)
    valid = [
        {"role": str(item["role"]), "content": str(item["content"])}
        for item in messages
        if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
    ]
    original_tokens = sum(estimate_tokens(item["content"]) for item in valid)
    selected: list[dict[str, str]] = []
    used = 0
    for item in reversed(valid):
        item_tokens = estimate_tokens(item["content"])
        if used + item_tokens > max_tokens:
            break
        selected.append(item)
        used += item_tokens
    if not selected and valid:
        newest = valid[-1]
        content = newest["content"]
        while estimate_tokens(content) > max_tokens and len(content) > 1:
            content = content[max(1, len(content) // 8) :]
        if content != newest["content"]:
            content = f"…{content}"
            while estimate_tokens(content) > max_tokens and len(content) > 1:
                content = f"…{content[2:]}"
        selected.append({"role": newest["role"], "content": content})
        used = estimate_tokens(content)
    selected.reverse()
    return ConversationContext(
        selected,
        used,
        original_tokens,
        len(selected) < len(valid),
        max_tokens,
    )


class ContextBudgetManager:
    def __init__(self, max_tokens: int = 1_200) -> None:
        if max_tokens < 64:
            raise ValueError("max_tokens must be at least 64")
        self.max_tokens = max_tokens

    def pack(
        self,
        query: str,
        chunks: list[str],
        strategy: Strategy = "relevant",
        *,
        full_documents: list[str] | None = None,
    ) -> ContextPack:
        original = "\n\n".join(chunks)
        original_tokens = estimate_tokens(original)
        tokens = {part.casefold() for part in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", query)}
        summarized = False
        if strategy == "full":
            source = full_documents or chunks
            original_tokens = estimate_tokens("\n\n".join(source))
            candidate = self._fit(source)
        elif strategy == "relevant":
            ranked = sorted(
                chunks,
                key=lambda chunk: sum(chunk.casefold().count(token) for token in tokens),
                reverse=True,
            )
            candidate = self._fit(ranked)
        elif strategy == "summary":
            ranked = sorted(
                chunks,
                key=lambda chunk: sum(chunk.casefold().count(token) for token in tokens),
                reverse=True,
            )
            condensed = [self._extractive_summary(chunk, tokens) for chunk in ranked]
            candidate = self._fit(condensed)
            summarized = any(a != b for a, b in zip(condensed, ranked, strict=True))
        else:
            raise ValueError("strategy must be full, relevant, or summary")

        used = estimate_tokens(candidate)
        return ContextPack(
            candidate,
            strategy,
            used,
            original_tokens,
            used < original_tokens,
            "estimate",
            summarized,
        )

    @staticmethod
    def _extractive_summary(chunk: str, tokens: set[str]) -> str:
        if estimate_tokens(chunk) <= 180:
            return chunk
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        source = lines[0] if lines else ""
        ranked = sorted(
            lines[1:],
            key=lambda line: sum(line.casefold().count(token) for token in tokens),
            reverse=True,
        )
        selected = ranked[:4]
        return "\n".join([source, *selected])[:900]

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
    mode: str = "delegated"
    token_source: str = "estimate"


class MetricsRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self,
        strategy: Strategy,
        context: ContextPack,
        output: str,
        success: bool,
        *,
        mode: str = "delegated",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        metric = RunMetric(
            strategy=strategy,
            input_tokens=input_tokens or context.estimated_tokens,
            output_tokens=output_tokens or estimate_tokens(output),
            success=success,
            timestamp=datetime.now(UTC).isoformat(),
            mode=mode,
            token_source=(
                "provider_usage"
                if input_tokens is not None or output_tokens is not None
                else context.token_source
            ),
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
