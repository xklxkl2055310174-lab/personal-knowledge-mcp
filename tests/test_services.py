from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.error
from email.message import Message

import pytest

from knowledge_mcp.config import Settings
from knowledge_mcp.services import KnowledgeServices
from knowledge_mcp.verification import Attempt, FailureClass, VerificationReport


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


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("上下文预算怎么控制", "预算"),
        ("为什么要做失败分类", "失败分类"),
        ("子Agent委派有什么好处", "子 Agent"),
    ],
)
def test_chinese_sentence_retrieval(settings: Settings, query: str, expected: str) -> None:
    (settings.knowledge_root / "notes" / "中文课程.md").write_text(
        "# Agent 课程\n上下文预算需要按相关性裁剪。\n"
        "失败分类决定参数错误不重试。\n子 Agent 委派能缩小权限。\n",
        encoding="utf-8",
    )
    result = KnowledgeServices(settings).search_notes(query, 5)
    assert result.ok
    assert any(expected in item["content"] for item in result.data["matches"])
    assert {"heading", "start_line", "end_line", "chunk_id"} <= result.data["matches"][0].keys()


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
async def test_file_access_rolls_back_failed_verification(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = settings.workspace_root / "rollback.txt"
    target.write_text("before", encoding="utf-8")

    async def failed(operation: object, verifier: object, **_: object) -> VerificationReport[dict]:
        await operation()  # type: ignore[operator]
        return VerificationReport(
            None,
            False,
            [Attempt(1, 1, "verification_failed", "transient", "verification_failed")],
            FailureClass("transient", "verification_failed", True, "failed"),
        )

    monkeypatch.setattr("knowledge_mcp.services.run_verified", failed)
    result = await KnowledgeServices(settings).file_access("write", "rollback.txt", "after")
    assert not result.ok
    assert result.meta["rolled_back"] is True
    assert result.meta["backup_id"]
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_git_query_is_read_only(settings: Settings) -> None:
    subprocess.run(["git", "init"], cwd=settings.repository_root, check=True, capture_output=True)
    (settings.repository_root / "example.txt").write_text("demo", encoding="utf-8")

    result = await KnowledgeServices(settings).git_query("status")

    assert result.ok
    assert "example.txt" in result.data["output"]
    assert result.data["files"][0]["path"] == "example.txt"


@pytest.mark.asyncio
async def test_git_timeout_is_classified(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*_: object, **__: object) -> None:
        raise subprocess.TimeoutExpired(["git"], 0.1)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = await KnowledgeServices(settings).git_query("status")
    assert not result.ok
    assert result.error and result.error.category == "timeout"


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
@pytest.mark.parametrize(
    ("status", "remaining", "category", "code"),
    [
        (404, None, "parameter", "github_repository_not_found"),
        (401, None, "permission", "github_unauthorized"),
        (403, "5", "permission", "github_forbidden"),
    ],
)
async def test_github_http_errors_are_precise(
    settings: Settings,
    status: int,
    remaining: str | None,
    category: str,
    code: str,
) -> None:
    headers = Message()
    if remaining is not None:
        headers["X-RateLimit-Remaining"] = remaining

    def urlopen(*_: object, **__: object) -> None:
        raise urllib.error.HTTPError("https://api.github.test", status, "failed", headers, None)

    result = await KnowledgeServices(settings, urlopen=urlopen).ci_status("github", "owner/repo")
    assert not result.ok
    assert result.error and result.error.category == category
    assert result.error.code == code


@pytest.mark.asyncio
async def test_command_sandbox_allows_python_and_blocks_shell(settings: Settings) -> None:
    services = KnowledgeServices(settings)
    script = settings.workspace_root / "safe.py"
    script.write_text("print('safe')\n", encoding="utf-8")

    allowed = await services.run_command(["python", "safe.py"])
    python_c = await services.run_command(["python", "-c", "print('unsafe')"])
    blocked = await services.run_command(["powershell", "-Command", "Write-Output unsafe"])

    assert allowed.ok
    assert allowed.data["returncode"] == 0
    assert allowed.data["stdout"].strip() == "safe"
    assert not python_c.ok
    assert python_c.error and python_c.error.category == "permission"
    assert not blocked.ok
    assert blocked.error and blocked.error.category == "permission"


@pytest.mark.asyncio
async def test_command_hides_secrets_and_blocks_git_writes(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-reach-child")
    script = settings.workspace_root / "env.py"
    script.write_text(
        "import os\nprint(os.getenv('GITHUB_TOKEN', '<missing>'))\n",
        encoding="utf-8",
    )
    services = KnowledgeServices(settings)
    environment = await services.run_command(["python", "env.py"])
    git_write = await services.run_command(["git", "commit", "-m", "blocked"])
    assert environment.ok
    assert environment.data["stdout"].strip() == "<missing>"
    assert "must-not-reach-child" not in environment.data["stdout"]
    assert not git_write.ok
    assert git_write.error and git_write.error.category == "permission"


@pytest.mark.asyncio
async def test_nonzero_command_is_observation_not_tool_failure(settings: Settings) -> None:
    script = settings.workspace_root / "exit.py"
    script.write_text("raise SystemExit(7)\n", encoding="utf-8")
    result = await KnowledgeServices(settings).run_command(["python", "exit.py"])
    assert result.ok
    assert result.data["exit_code"] == 7
    assert result.meta["attempts"][0]["outcome"] == "verified"
