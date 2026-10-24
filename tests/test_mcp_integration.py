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
        tools = (await client.list_tools()).tools
        names = {tool.name for tool in tools}

    assert names == {"search_notes", "file_access", "git_query", "ci_status", "run_command"}
    file_tool = next(tool for tool in tools if tool.name == "file_access")
    properties = file_tool.input_schema["properties"]
    assert properties["action"]["enum"] == ["list", "read", "write", "replace"]
    assert {
        "content",
        "mode",
        "expected_sha256",
        "old_text",
        "new_text",
    }.issubset(properties)
    for tool in tools:
        assert tool.description
        assert all(
            schema.get("description") for schema in tool.input_schema.get("properties", {}).values()
        )
    assert next(tool for tool in tools if tool.name == "search_notes").annotations.read_only_hint
    assert next(tool for tool in tools if tool.name == "ci_status").annotations.open_world_hint
    assert next(tool for tool in tools if tool.name == "file_access").annotations.destructive_hint


@pytest.mark.asyncio
async def test_tool_failure_sets_mcp_is_error_and_keeps_envelope(settings: Settings) -> None:
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("search_notes", {"query": "", "top_k": 5})
    assert result.is_error is True
    assert result.structured_content["ok"] is False
    assert result.structured_content["error"]["category"] == "parameter"
    assert {
        "trace_id",
        "tool",
        "duration_ms",
        "attempts",
        "caller",
    } <= result.structured_content["meta"].keys()


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
