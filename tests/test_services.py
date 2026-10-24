from __future__ import annotations

import hashlib
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
    assert read.data["sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert not escaped.ok
    assert escaped.error and escaped.error.category == "permission"


@pytest.mark.asyncio
async def test_file_access_create_mode_and_hash_conflict(settings: Settings) -> None:
    services = KnowledgeServices(settings)

    created = await services.file_access(
        "write", "notes/versioned.txt", "version one", mode="create"
    )
    duplicate = await services.file_access(
        "write", "notes/versioned.txt", "must not replace", mode="create"
    )
    stale_hash = created.data["sha256"]
    (settings.workspace_root / "notes" / "versioned.txt").write_text(
        "external change", encoding="utf-8"
    )
    conflicted = await services.file_access(
        "write",
        "notes/versioned.txt",
        "must not replace",
        expected_sha256=stale_hash,
    )

    assert created.ok and created.data["sha256"] == hashlib.sha256(b"version one").hexdigest()
    assert not duplicate.ok
    assert duplicate.error and duplicate.error.code == "file_exists"
    assert duplicate.error.category == "conflict" and not duplicate.error.retryable
    assert not conflicted.ok
    assert conflicted.error and conflicted.error.code == "file_changed"
    assert conflicted.error.category == "conflict" and not conflicted.error.retryable
    assert (settings.workspace_root / "notes" / "versioned.txt").read_text(
        encoding="utf-8"
    ) == "external change"


@pytest.mark.asyncio
async def test_file_access_replaces_one_exact_block_and_verifies_hash(settings: Settings) -> None:
    services = KnowledgeServices(settings)
    original = await services.file_access(
        "write", "notes/replace.md", "before\nunique block\nafter\n"
    )

    replaced = await services.file_access(
        "replace",
        "notes/replace.md",
        expected_sha256=original.data["sha256"],
        old_text="unique block",
        new_text="updated block",
    )
    read = await services.file_access("read", "notes/replace.md")

    assert replaced.ok and replaced.meta["verified"] is True
    assert replaced.data["replacements"] == 1
    assert replaced.data["previous_sha256"] == original.data["sha256"]
    assert replaced.data["sha256"] == read.data["sha256"]
    assert read.data["content"] == "before\nupdated block\nafter\n"


@pytest.mark.asyncio
async def test_file_access_replace_rejects_missing_ambiguous_and_unsafe_paths(
    settings: Settings,
) -> None:
    services = KnowledgeServices(settings)
    target = settings.workspace_root / "notes" / "ambiguous.txt"
    target.parent.mkdir(parents=True)
    target.write_text("same same", encoding="utf-8")

    missing = await services.file_access(
        "replace", "notes/ambiguous.txt", old_text="absent", new_text="new"
    )
    ambiguous = await services.file_access(
        "replace", "notes/ambiguous.txt", old_text="same", new_text="new"
    )
    escaped = await services.file_access(
        "replace", "../outside.txt", old_text="old", new_text="new"
    )

    assert not missing.ok and missing.error and missing.error.code == "old_text_not_found"
    assert missing.error.category == "conflict"
    assert not ambiguous.ok and ambiguous.error and ambiguous.error.code == "ambiguous_old_text"
    assert ambiguous.error.details["occurrences"] == 2
    assert not escaped.ok and escaped.error and escaped.error.category == "permission"
    assert target.read_text(encoding="utf-8") == "same same"


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
