from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from knowledge_mcp.config import Settings
from knowledge_mcp.providers import Completion
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


def test_agent_ask_uses_configured_llm_without_exposing_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProviderClient:
        def __init__(self, config: object) -> None:
            self.config = config

        async def answer(self, query: str, context: str) -> Completion:
            assert query == "MCP Agent"
            assert "mcp.md:3" in context
            return Completion("真实 LLM 基于 MCP 证据回答", 21, 8)

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
    assert data["answer"] == "真实 LLM 基于 MCP 证据回答"
    assert data["steps"][3]["detail"]["mode"] == "llm"
    assert data["steps"][3]["detail"]["model"] == "interview-model"
    assert "must-not-leak" not in response.text


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
