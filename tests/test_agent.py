from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from knowledge_mcp.agent import DelegatedAgent, ToolCallingAgent
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
            assert "受工具权限约束" in messages[0]["content"]
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
    assert result.detail["tools_used"] == ["search_notes"]
    assert result.detail["citations_valid"] is True
    assert result.detail["evidence"] == ["mcp.md:3 | MCP connects tools"]


class FailingToolClient(FakeToolClient):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return SimpleNamespace(
            structured_content={
                "ok": False,
                "data": None,
                "error": {
                    "category": "parameter",
                    "code": "invalid_query",
                    "message": "bad query",
                    "retryable": False,
                },
                "meta": {},
            }
        )


class RepeatingProvider(FakeProvider):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        self.turn += 1
        if self.turn <= 2:
            return ModelTurn(
                "",
                (ToolCall(f"call-{self.turn}", "search_notes", {"query": ""}),),
                2,
                1,
            )
        return ModelTurn("无证据：参数错误，无法继续检索。", (), 2, 4)


@pytest.mark.asyncio
async def test_single_agent_blocks_identical_non_retryable_call() -> None:
    client = FailingToolClient()
    result = await ToolCallingAgent(
        client,
        RepeatingProvider(),  # type: ignore[arg-type]
        ContextBudgetManager(500),
    ).run("检索")

    assert client.calls == [("search_notes", {"query": ""})]
    assert result.detail["tool_calls"] == 1
    assert result.detail["tool_call_log"][1]["called"] is False
    assert result.detail["tool_call_log"][1]["code"] == "repeated_non_retryable_call"


class CitationRepairProvider(FakeProvider):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        self.turn += 1
        if self.turn == 1:
            return ModelTurn(
                "",
                (ToolCall("call-1", "search_notes", {"query": "MCP"}),),
                2,
                1,
            )
        if self.turn == 2:
            return ModelTurn("错误引用（mcp.md:99）", (), 2, 3)
        assert "引用校验未通过" in messages[-1]["content"]
        return ModelTurn("正确引用（mcp.md:3）", (), 2, 3)


@pytest.mark.asyncio
async def test_single_agent_requests_revision_for_unknown_citation() -> None:
    result = await ToolCallingAgent(
        FakeToolClient(),
        CitationRepairProvider(),  # type: ignore[arg-type]
        ContextBudgetManager(500),
    ).run("MCP 是什么")

    assert result.text == "正确引用（mcp.md:3）"
    assert result.detail["citation_revisions"] == 1
    assert result.detail["citations_valid"] is True


class SearchLimitProvider(FakeProvider):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        self.turn += 1
        if self.turn <= 4:
            return ModelTurn(
                "",
                (
                    ToolCall(
                        f"call-{self.turn}",
                        "search_notes",
                        {"query": f"MCP 查询 {self.turn}", "top_k": 5},
                    ),
                ),
                2,
                1,
            )
        return ModelTurn("MCP 连接工具（mcp.md:3）", (), 2, 3)


@pytest.mark.asyncio
async def test_knowledge_search_is_limited_and_evidence_is_deduplicated() -> None:
    client = FakeToolClient()
    result = await ToolCallingAgent(
        client,
        SearchLimitProvider(),  # type: ignore[arg-type]
        ContextBudgetManager(500),
        max_steps=6,
        tool_call_limits={"search_notes": 3},
    ).run("MCP 是什么")

    assert len(client.calls) == 3
    assert result.detail["tool_calls"] == 3
    assert result.detail["evidence"] == ["mcp.md:3 | MCP connects tools"]
    log = result.detail["tool_call_log"]
    assert [item["called"] for item in log] == [True, True, True, False]
    assert log[3]["code"] == "tool_call_limit_exceeded"
    assert log[3]["call_limit"] == 3
    assert log[1]["duplicates_removed"] == 1
    assert log[1]["unique_matches"] == 0
    assert log[0]["context"]["text"]
    assert log[0]["arguments"] == {"query": "MCP 查询 1", "top_k": 5}


class ClaimCoverageProvider(FakeProvider):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        self.turn += 1
        if self.turn == 1:
            return ModelTurn(
                "",
                (ToolCall("call-1", "search_notes", {"query": "MCP"}),),
                2,
                1,
            )
        if self.turn == 2:
            return ModelTurn(
                "- MCP 连接工具（mcp.md:3）\n- 它可以直接访问任意文件。",
                (),
                2,
                6,
            )
        assert "未覆盖结论" in messages[-1]["content"]
        return ModelTurn(
            "- MCP 连接工具（mcp.md:3）\n- 任意文件访问：无证据。",
            (),
            2,
            6,
        )


@pytest.mark.asyncio
async def test_each_substantive_claim_requires_evidence_or_no_evidence_marker() -> None:
    result = await ToolCallingAgent(
        FakeToolClient(),
        ClaimCoverageProvider(),  # type: ignore[arg-type]
        ContextBudgetManager(500),
    ).run("MCP 是什么")

    coverage = result.detail["citation_coverage"]
    assert result.detail["citation_revisions"] == 1
    assert coverage["valid"] is True
    assert coverage["total_claims"] == 2
    assert coverage["covered_claims"] == 2
    assert coverage["coverage_rate"] == 1.0


class DelegationProvider(FakeProvider):
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        system = messages[0]["content"]
        has_tool_result = any(message["role"] == "tool" for message in messages)
        if system.startswith("你是多 Agent 系统中的协调 Agent"):
            assert {tool["function"]["name"] for tool in tools} == {
                "delegate_knowledge",
                "delegate_files",
                "delegate_git",
                "delegate_ci",
                "delegate_command",
            }
            if not has_tool_result:
                return ModelTurn(
                    "",
                    (ToolCall("delegate-1", "delegate_knowledge", {"task": "检索 MCP 是什么"}),),
                    5,
                    2,
                )
            return ModelTurn("MCP 连接 Agent 与工具（mcp.md:3）。", (), 8, 6)

        assert "知识检索子 Agent" in system
        assert [tool["function"]["name"] for tool in tools] == ["search_notes"]
        if not has_tool_result:
            return ModelTurn(
                "",
                (ToolCall("search-1", "search_notes", {"query": "MCP"}),),
                4,
                2,
            )
        return ModelTurn("证据表明 MCP 连接工具（mcp.md:3）。", (), 6, 5)


@pytest.mark.asyncio
async def test_delegated_agent_routes_to_least_privilege_specialist() -> None:
    client = FakeToolClient()
    result = await DelegatedAgent(
        client,
        DelegationProvider(),  # type: ignore[arg-type]
        ContextBudgetManager(500),
    ).run("MCP 是什么")

    assert client.calls == [("search_notes", {"query": "MCP"})]
    assert result.text == "MCP 连接 Agent 与工具（mcp.md:3）。"
    assert result.detail["mode"] == "delegated"
    assert result.detail["specialists_used"] == ["delegate_knowledge"]
    assert result.detail["tools_used"] == ["search_notes"]
    assert result.detail["tool_calls"] == 1
    assert result.detail["citations_valid"] is True
