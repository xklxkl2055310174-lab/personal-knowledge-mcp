from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from knowledge_mcp.config import Settings
from knowledge_mcp.providers import Completion, ModelTurn, ProviderConfig, ToolCall
from knowledge_mcp.server import create_server


@pytest.fixture
def client(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    notes = settings.knowledge_root / "notes"
    (notes / "mcp.md").write_text(
        "# MCP\n\nMCP separates Agent reasoning from tool execution.\n", encoding="utf-8"
    )
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=app></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("MCP_WEB_DIST", str(dist))
    server = create_server(settings)
    app = server.streamable_http_app(json_response=True, stateless_http=True)
    with TestClient(app) as test_client:
        yield test_client


def test_overview_reports_config_and_notes(client: TestClient) -> None:
    body = client.get("/api/overview").json()

    assert body["ok"] is True
    assert body["data"]["mcp_endpoint"] == "/mcp"
    assert body["data"]["notes_count"] == 1
    assert "python" in body["data"]["config"]["allowed_commands"]


def test_provider_catalog_does_not_return_secrets(client: TestClient) -> None:
    body = client.get("/api/providers").json()

    assert body["ok"] is True
    ids = {item["id"] for item in body["data"]["providers"]}
    assert {"local", "openai", "anthropic", "gemini", "deepseek"} <= ids
    assert "api_key" not in json.dumps(body)


def test_provider_can_be_saved_and_reused_without_returning_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "knowledge_mcp.provider_store.protect_secret",
        lambda value: value[::-1],
    )
    monkeypatch.setattr(
        "knowledge_mcp.provider_store.unprotect_secret",
        lambda value: value[::-1],
    )
    saved = client.post(
        "/api/provider/save",
        json={
            "provider": {
                "id": "deepseek",
                "model": "deepseek-chat",
                "api_key": "must-not-return",
            }
        },
    )

    assert saved.status_code == 200
    assert "must-not-return" not in saved.text
    catalog = client.get("/api/providers").json()["data"]
    assert catalog["saved_provider"]["id"] == "deepseek"
    assert "api_key" not in json.dumps(catalog)

    class FakeProviderClient:
        def __init__(self, config: ProviderConfig) -> None:
            assert config.api_key == "must-not-return"

        async def answer(self, query: str, context: str) -> Completion:
            return Completion("连接成功", 2, 2)

    monkeypatch.setattr("knowledge_mcp.web.ProviderClient", FakeProviderClient)
    tested = client.post(
        "/api/provider/test",
        json={"provider": {"use_saved": True}},
    )
    assert tested.status_code == 200
    assert tested.json()["data"]["connected"] is True
    assert "must-not-return" not in tested.text

    deleted = client.delete("/api/provider/saved")
    assert deleted.status_code == 200
    assert client.get("/api/providers").json()["data"]["saved_provider"] is None


def test_notes_list_and_content(client: TestClient) -> None:
    notes = client.get("/api/notes").json()["data"]["notes"]
    assert [item["path"] for item in notes] == ["mcp.md"]

    content = client.get("/api/notes/content", params={"path": "mcp.md"}).json()
    assert "MCP separates" in content["data"]["content"]


def test_note_content_rejects_path_escape(client: TestClient) -> None:
    response = client.get("/api/notes/content", params={"path": "../../etc/passwd"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "path_not_allowed"


def test_agent_ask_returns_five_stage_trace(client: TestClient) -> None:
    response = client.post(
        "/api/agent/ask", json={"query": "MCP Agent", "strategy": "relevant", "max_tokens": 200}
    )
    data = response.json()["data"]

    assert response.status_code == 200
    assert [step["name"] for step in data["steps"]] == [
        "intake",
        "search",
        "context",
        "summary",
        "writeback",
    ]
    assert "mcp.md:3" in data["answer"]
    assert data["context"]["estimated_tokens"] <= 200


def test_delegated_agent_uses_configured_llm_without_exposing_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProviderClient:
        def __init__(self, config: object) -> None:
            self.config = config

        async def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
            system = messages[0]["content"]
            has_tool_result = any(message["role"] == "tool" for message in messages)
            if system.startswith("你是多 Agent 系统中的协调 Agent"):
                if not has_tool_result:
                    return ModelTurn(
                        "",
                        (
                            ToolCall(
                                "delegate-1",
                                "delegate_knowledge",
                                {"task": "检索并回答 MCP Agent"},
                            ),
                        ),
                        5,
                        2,
                    )
                return ModelTurn("真实 LLM 基于 MCP 证据回答（mcp.md:3）", (), 8, 8)
            if not has_tool_result:
                return ModelTurn(
                    "",
                    (ToolCall("search-1", "search_notes", {"query": "MCP Agent"}),),
                    5,
                    2,
                )
            return ModelTurn("子 Agent 找到证据（mcp.md:3）", (), 8, 6)

    monkeypatch.setattr("knowledge_mcp.web.ProviderClient", FakeProviderClient)
    response = client.post(
        "/api/agent/ask",
        json={
            "query": "MCP Agent",
            "strategy": "relevant",
            "max_tokens": 200,
            "provider": {
                "id": "custom",
                "model": "interview-model",
                "base_url": "http://127.0.0.1:9999/v1",
                "api_key": "must-not-leak",
            },
        },
    )
    data = response.json()["data"]

    assert response.status_code == 200
    assert data["answer"] == "真实 LLM 基于 MCP 证据回答（mcp.md:3）"
    assert [step["name"] for step in data["steps"]] == [
        "intake",
        "delegation_1",
        "coordination",
    ]
    assert data["steps"][1]["title"] == "委派 1 · 知识检索子 Agent"
    assert data["steps"][1]["detail"]["tools_used"] == ["search_notes"]
    search_log = data["steps"][1]["detail"]["tool_call_log"]
    assert search_log[0]["arguments"] == {"query": "MCP Agent", "top_k": 5}
    assert search_log[0]["call_limit"] == 3
    assert search_log[0]["context"]["text"]
    assert search_log[0]["context"]["truncated"] is False
    assert data["steps"][2]["detail"]["mode"] == "delegated"
    assert data["steps"][2]["detail"]["model"] == "interview-model"
    assert data["steps"][2]["detail"]["specialists_used"] == ["delegate_knowledge"]
    assert "must-not-leak" not in response.text


def test_single_agent_recovers_from_file_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeToolProviderClient:
        def __init__(self, config: object) -> None:
            self.config = config
            self.turn = 0

        async def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
            self.turn += 1
            if self.turn == 1:
                return ModelTurn(
                    "",
                    (ToolCall("search-1", "search_notes", {"query": "MCP", "top_k": 5}),),
                    10,
                    2,
                )
            if self.turn == 2:
                return ModelTurn(
                    "",
                    (
                        ToolCall(
                            "read-1",
                            "file_access",
                            {"action": "read", "path": "missing.md"},
                        ),
                    ),
                    12,
                    2,
                )
            return ModelTurn(
                "文件不存在，但知识库证据表明 MCP 分离推理和执行（mcp.md:3）。",
                (),
                14,
                9,
            )

    monkeypatch.setattr("knowledge_mcp.web.ProviderClient", FakeToolProviderClient)
    response = client.post(
        "/api/agent/ask",
        json={
            "query": "MCP 是什么",
            "strategy": "relevant",
            "max_tokens": 200,
            "mode": "single",
            "provider": {
                "id": "custom",
                "model": "tool-model",
                "base_url": "http://127.0.0.1:9999/v1",
                "api_key": "temporary-test-key",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "single"
    assert data["citations_valid"] is True
    assert data["evidence"] == [
        "mcp.md:3 | MCP separates Agent reasoning from tool execution."
    ]
    log = data["steps"][0]["detail"]["tool_call_log"]
    assert [item["tool"] for item in log] == ["search_notes", "file_access"]
    assert log[1]["code"] == "file_not_found"


def test_single_agent_nested_runtime_error_is_not_http_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingProviderClient:
        def __init__(self, config: object) -> None:
            self.config = config

        async def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
            raise RuntimeError("synthetic agent failure")

    monkeypatch.setattr("knowledge_mcp.web.ProviderClient", FailingProviderClient)
    response = client.post(
        "/api/agent/ask",
        json={
            "query": "检查 Git",
            "mode": "single",
            "provider": {
                "id": "custom",
                "model": "tool-model",
                "base_url": "http://127.0.0.1:9999/v1",
                "api_key": "temporary-test-key",
            },
        },
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "agent_failed"
    assert error["message"] == "synthetic agent failure"


def test_conversation_memory_is_injected_and_can_be_cleared(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_messages: list[list[dict]] = []

    class MemoryProviderClient:
        def __init__(self, config: ProviderConfig) -> None:
            self.config = config

        async def chat(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
            seen_messages.append(messages)
            number = len(seen_messages)
            return ModelTurn(f"第 {number} 次回答", (), 8, 4)

    monkeypatch.setattr("knowledge_mcp.web.ProviderClient", MemoryProviderClient)
    provider = {
        "id": "custom",
        "model": "memory-model",
        "base_url": "http://127.0.0.1:9999/v1",
        "api_key": "temporary-test-key",
    }
    conversation_id = "memory_case_01"
    first = client.post(
        "/api/agent/ask",
        json={
            "query": "我的项目是什么？",
            "mode": "single",
            "conversation_id": conversation_id,
            "provider": provider,
        },
    )
    second = client.post(
        "/api/agent/ask",
        json={
            "query": "它有哪些工具？",
            "mode": "single",
            "conversation_id": conversation_id,
            "provider": provider,
        },
    )

    assert first.status_code == 200 and second.status_code == 200
    assert [item["role"] for item in seen_messages[1]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert seen_messages[1][1]["content"] == "我的项目是什么？"
    assert seen_messages[1][2]["content"] == "第 1 次回答"
    second_data = second.json()["data"]
    assert second_data["conversation_id"] == conversation_id
    assert second_data["memory"]["injected_messages"] == 2

    history = client.get(f"/api/conversations/{conversation_id}").json()["data"]
    assert history["message_count"] == 4
    assert history["messages"][-1]["content"] == "第 2 次回答"
    cleared = client.delete(f"/api/conversations/{conversation_id}").json()["data"]
    assert cleared["cleared"] is True
    assert client.get(f"/api/conversations/{conversation_id}").json()["data"][
        "message_count"
    ] == 0


def test_agent_compare_runs_all_strategies(client: TestClient) -> None:
    data = client.post("/api/agent/compare", json={"query": "MCP"}).json()["data"]

    assert [row["strategy"] for row in data["rows"]] == ["full", "relevant", "summary"]
    assert client.get("/api/agent/metrics").json()["data"]["runs"] == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "MCP", "strategy": "everything"},
        {"query": "MCP", "max_tokens": 1},
    ],
)
def test_agent_ask_validates_input(client: TestClient, payload: dict) -> None:
    response = client.post("/api/agent/ask", json=payload)

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_spa_serves_assets_and_history_fallback(client: TestClient) -> None:
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "id=app" in client.get("/agent").text
    assert client.get("/api/unknown").status_code == 404
    assert client.get("/health").json()["status"] == "ok"


def test_task_history_trace_events_and_metrics(client: TestClient) -> None:
    created = client.post(
        "/api/tasks",
        json={
            "query": "MCP Agent",
            "mode": "delegated",
            "strategy": "relevant",
            "max_tokens": 200,
        },
    ).json()["data"]
    task_id = created["task_id"]
    detail = None
    for _ in range(50):
        detail = client.get(f"/api/tasks/{task_id}").json()["data"]
        if detail["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert detail and detail["status"] == "completed"
    assert detail["answer"]
    listing = client.get("/api/tasks", params={"limit": 10, "offset": 0}).json()["data"]
    assert any(item["id"] == task_id for item in listing["items"])
    with client.stream("GET", f"/api/tasks/{task_id}/events") as response:
        body = "".join(response.iter_text())
    assert "task_finished" in body
    assert client.get("/api/tool-calls").json()["ok"] is True
    assert client.get("/api/metrics/failures").json()["ok"] is True
    assert client.get("/api/metrics/strategies").json()["ok"] is True
    assert client.get("/api/metrics/delegation").json()["ok"] is True


def test_note_index_api_reindexes(client: TestClient) -> None:
    before = client.get("/api/notes/index").json()["data"]
    after = client.post("/api/notes/reindex").json()["data"]
    assert before["files"] >= 0
    assert after["files"] == 1
    assert after["chunks"] >= 1


def test_eval_report_endpoints_are_backward_safe(client: TestClient) -> None:
    listing = client.get("/api/eval/reports").json()
    assert listing["ok"] is True
    missing = client.get("/api/eval/reports/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "report_not_found"
