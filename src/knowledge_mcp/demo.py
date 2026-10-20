"""Command-line demo client and two-child-agent workflow."""

from __future__ import annotations

import argparse
import asyncio

from mcp import Client

from .agent import KnowledgeAgent
from .config import Settings
from .context import ContextBudgetManager, MetricsRecorder


async def run(query: str, server_url: str, strategy: str) -> None:
    settings = Settings.from_env()
    async with Client(server_url) as client:
        agent = KnowledgeAgent(
            client=client,
            context_manager=ContextBudgetManager(max_tokens=800),
            metrics=MetricsRecorder(settings.runtime_root / "agent_metrics.jsonl"),
        )
        answer = await agent.answer(query, strategy=strategy)  # type: ignore[arg-type]
        print(answer)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the learning knowledge Agent")
    parser.add_argument("query", help="Question to search for in local notes")
    parser.add_argument("--server", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--strategy", choices=("full", "relevant", "summary"), default="relevant")
    args = parser.parse_args()
    asyncio.run(run(args.query, args.server, args.strategy))


if __name__ == "__main__":
    main()
