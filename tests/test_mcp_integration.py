from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client

from knowledge_mcp.agent import KnowledgeAgent
from knowledge_mcp.config import Settings
from knowledge_mcp.context import ContextBudgetManager, MetricsRecorder
from knowledge_mcp.server import create_server


@pytest.mark.asyncio
async def test_all_five_tools_are_discoverable(settings: Settings) -> None:
    server = create_server(settings)
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert names == {"search_notes", "file_access", "git_query", "ci_status", "run_command"}


@pytest.mark.asyncio
async def test_agent_delegates_search_and_summary(settings: Settings, tmp_path: Path) -> None:
    (settings.knowledge_root / "notes" / "learning.md").write_text(
        "MCP separates Agent reasoning from tool execution.", encoding="utf-8"
    )
    server = create_server(settings)

    async with Client(server) as client:
        agent = KnowledgeAgent(
            client,
            ContextBudgetManager(200),
            MetricsRecorder(tmp_path / "metrics.jsonl"),
        )
        answer = await agent.answer("MCP Agent")

    assert "learning.md:1" in answer
    assert "MCP separates" in answer
