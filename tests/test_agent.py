from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from knowledge_mcp.agent import ToolCallingAgent
from knowledge_mcp.context import ContextBudgetManager
from knowledge_mcp.providers import ModelTurn, ToolCall


class FakeToolClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> Any:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="search_notes",
                    description="search",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                )
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return SimpleNamespace(
            structured_content={
                "ok": True,
                "data": {
                    "matches": [
                        {
                            "path": "mcp.md",
                            "line": 3,
                            "snippet": "MCP connects tools",
                        }
                    ]
                },
                "error": None,
                "meta": {},
            }
        )


class FakeProvider:
    def __init__(self) -> None:
        self.turn = 0
        self.config = SimpleNamespace(
            public=lambda: {
                "id": "fake",
                "model": "fake-model",
                "protocol": "openai",
                "supports_tools": True,
            }
        )

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        self.turn += 1
        if self.turn == 1:
            assert tools[0]["function"]["name"] == "search_notes"
            return ModelTurn(
                "",
                (ToolCall("call-1", "search_notes", {"query": "MCP"}),),
                10,
                2,
            )
        assert any(message["role"] == "tool" for message in messages)
        return ModelTurn("MCP connects tools（mcp.md:3）", (), 20, 8)


@pytest.mark.asyncio
async def test_single_agent_uses_discovered_tool_through_mcp_client() -> None:
    client = FakeToolClient()
    result = await ToolCallingAgent(
        client,  # type: ignore[arg-type]
        FakeProvider(),  # type: ignore[arg-type]
        ContextBudgetManager(500),
    ).run("MCP 是什么")
    assert result.text.endswith("mcp.md:3）")
    assert client.calls == [("search_notes", {"query": "MCP"})]
    assert result.detail["tool_calls"] == 1
