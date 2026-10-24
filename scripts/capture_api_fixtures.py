"""Capture real TestClient responses for every additive HTTP API."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from starlette.testclient import TestClient

from knowledge_mcp.config import Settings
from knowledge_mcp.server import create_server


def dump(root: Path, name: str, value: object) -> None:
    (root / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    fixture_root = project / "docs" / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="knowledge-mcp-api-") as directory:
        root = Path(directory)
        settings = Settings(
            project_root=root,
            knowledge_root=root / "knowledge",
            workspace_root=root / "workspace",
            repository_root=root / "repository",
            runtime_root=root / "runtime",
        )
        settings.ensure_directories()
        settings.repository_root.mkdir()
        (settings.knowledge_root / "notes" / "sample.md").write_text(
            "# MCP\n\nMCP 让 Agent 通过标准协议调用工具。\n",
            encoding="utf-8",
        )
        report_root = root / "reports"
        report_root.mkdir()
        (report_root / "sample.json").write_text(
            json.dumps({"mode": "local", "task_count": 30}, ensure_ascii=False),
            encoding="utf-8",
        )
        app = create_server(settings).streamable_http_app(json_response=True, stateless_http=True)
        with TestClient(app) as client:
            dump(fixture_root, "notes-index.json", client.get("/api/notes/index").json())
            dump(
                fixture_root,
                "notes-reindex.json",
                client.post("/api/notes/reindex").json(),
            )
            created = client.post(
                "/api/tasks",
                json={
                    "query": "MCP 如何连接 Agent 与工具",
                    "mode": "delegated",
                    "strategy": "relevant",
                    "max_tokens": 200,
                },
            ).json()
            dump(fixture_root, "tasks-create.json", created)
            task_id = created["data"]["task_id"]
            detail = None
            for _ in range(100):
                detail = client.get(f"/api/tasks/{task_id}").json()
                if detail["data"]["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            dump(fixture_root, "task-detail.json", detail)
            dump(fixture_root, "tasks-list.json", client.get("/api/tasks").json())
            with client.stream("GET", f"/api/tasks/{task_id}/events") as response:
                (fixture_root / "task-events.sse.txt").write_text(
                    "".join(response.iter_text()), encoding="utf-8"
                )
            dump(fixture_root, "tool-calls.json", client.get("/api/tool-calls").json())
            dump(
                fixture_root,
                "metrics-failures.json",
                client.get("/api/metrics/failures").json(),
            )
            dump(
                fixture_root,
                "metrics-strategies.json",
                client.get("/api/metrics/strategies").json(),
            )
            dump(
                fixture_root,
                "metrics-delegation.json",
                client.get("/api/metrics/delegation").json(),
            )
            dump(
                fixture_root,
                "eval-reports.json",
                client.get("/api/eval/reports").json(),
            )
            dump(
                fixture_root,
                "eval-report-detail.json",
                client.get("/api/eval/reports/sample").json(),
            )


if __name__ == "__main__":
    main()
