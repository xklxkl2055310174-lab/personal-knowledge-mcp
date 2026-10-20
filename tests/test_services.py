from __future__ import annotations

import json
import subprocess

import pytest

from knowledge_mcp.config import Settings
from knowledge_mcp.services import KnowledgeServices


def test_search_notes_returns_ranked_line(settings: Settings) -> None:
    note = settings.knowledge_root / "notes" / "mcp-basics.md"
    note.write_text("# MCP\nMCP connects an Agent to tools.\nUnrelated line.\n", encoding="utf-8")

    result = KnowledgeServices(settings).search_notes("MCP Agent", top_k=3)

    assert result.ok
    assert result.data["matches"][0]["path"] == "mcp-basics.md"
    assert result.data["matches"][0]["line"] == 2


def test_search_rejects_bad_top_k(settings: Settings) -> None:
    result = KnowledgeServices(settings).search_notes("MCP", top_k=0)
    assert not result.ok
    assert result.error and result.error.category == "parameter"


@pytest.mark.asyncio
async def test_file_access_writes_reads_and_blocks_traversal(settings: Settings) -> None:
    services = KnowledgeServices(settings)

    written = await services.file_access("write", "notes/demo.txt", "hello")
    read = await services.file_access("read", "notes/demo.txt")
    escaped = await services.file_access("read", "../outside.txt")

    assert written.ok and written.meta["verified"] is True
    assert read.ok and read.data["content"] == "hello"
    assert not escaped.ok
    assert escaped.error and escaped.error.category == "permission"


@pytest.mark.asyncio
async def test_git_query_is_read_only(settings: Settings) -> None:
    subprocess.run(["git", "init"], cwd=settings.repository_root, check=True, capture_output=True)
    (settings.repository_root / "example.txt").write_text("demo", encoding="utf-8")

    result = await KnowledgeServices(settings).git_query("status")

    assert result.ok
    assert "example.txt" in result.data["output"]


def test_local_ci_status(settings: Settings) -> None:
    workflows = settings.repository_root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: CI", encoding="utf-8")
    status_dir = settings.repository_root / ".ci"
    status_dir.mkdir()
    (status_dir / "status.json").write_text(
        json.dumps({"state": "passed", "commit": "abc"}), encoding="utf-8"
    )

    result = KnowledgeServices(settings)._local_ci_status()

    assert result.ok
    assert result.data["state"] == "passed"
    assert result.data["workflows"] == ["ci.yml"]


@pytest.mark.asyncio
async def test_command_sandbox_allows_python_and_blocks_shell(settings: Settings) -> None:
    services = KnowledgeServices(settings)

    allowed = await services.run_command(["python", "-c", "print('safe')"])
    blocked = await services.run_command(["powershell", "-Command", "Write-Output unsafe"])

    assert allowed.ok
    assert allowed.data["returncode"] == 0
    assert allowed.data["stdout"].strip() == "safe"
    assert not blocked.ok
    assert blocked.error and blocked.error.category == "permission"
