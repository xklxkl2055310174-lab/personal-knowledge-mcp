"""Implementations behind the five MCP tools."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Literal

from .config import Settings
from .contracts import ToolResult, failure, success
from .security import SecurityViolation, executable_name, resolve_inside
from .verification import run_verified

TEXT_SUFFIXES = {".md", ".txt", ".rst"}
WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _attempts_meta(report: Any) -> list[dict[str, Any]]:
    return [
        {"number": item.number, "duration_ms": item.duration_ms, "outcome": item.outcome}
        for item in report.attempts
    ]


class KnowledgeServices:
    def __init__(
        self,
        settings: Settings,
        *,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.settings = settings
        self.urlopen = urlopen

    def search_notes(self, query: str, top_k: int = 5) -> ToolResult:
        query = query.strip()
        if not query:
            return failure("empty_query", "query must not be empty", "parameter")
        if not 1 <= top_k <= 20:
            return failure("invalid_top_k", "top_k must be between 1 and 20", "parameter")

        tokens = {token.casefold() for token in WORD_RE.findall(query)}
        notes_root = self.settings.knowledge_root / "notes"
        matches: list[dict[str, Any]] = []
        scanned_files = 0

        for path in sorted(notes_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            scanned_files += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if len(text.encode("utf-8")) > self.settings.max_file_bytes:
                continue

            relative = path.relative_to(notes_root).as_posix()
            filename = path.stem.casefold()
            for line_number, line in enumerate(text.splitlines(), start=1):
                folded = line.casefold()
                hit_count = sum(folded.count(token) for token in tokens)
                if hit_count == 0:
                    continue
                filename_bonus = sum(2 for token in tokens if token in filename)
                phrase_bonus = 3 if query.casefold() in folded else 0
                score = hit_count + filename_bonus + phrase_bonus
                matches.append(
                    {
                        "path": relative,
                        "line": line_number,
                        "score": score,
                        "snippet": line.strip()[:320],
                    }
                )

        matches.sort(key=lambda item: (-item["score"], item["path"], item["line"]))
        selected = matches[:top_k]
        return success(
            {"query": query, "matches": selected},
            scanned_files=scanned_files,
            total_matches=len(matches),
            truncated=len(matches) > len(selected),
        )

    async def file_access(
        self,
        action: Literal["read", "write", "list"],
        path: str = ".",
        content: str | None = None,
    ) -> ToolResult:
        try:
            target = resolve_inside(self.settings.workspace_root, path)
        except SecurityViolation as exc:
            return failure("path_not_allowed", str(exc), "permission")

        if action == "list":
            if not target.exists() or not target.is_dir():
                return failure("directory_not_found", "Directory does not exist", "parameter")
            entries = [
                {
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
                for child in sorted(
                    target.iterdir(), key=lambda item: (not item.is_dir(), item.name)
                )
            ]
            return success({"path": path, "entries": entries[:200]}, truncated=len(entries) > 200)

        if action == "read":
            if not target.exists() or not target.is_file():
                return failure("file_not_found", "File does not exist", "parameter")
            if target.stat().st_size > self.settings.max_file_bytes:
                return failure("file_too_large", "File exceeds MCP_MAX_FILE_BYTES", "parameter")
            try:
                value = target.read_text(encoding="utf-8")
            except UnicodeError:
                return failure("not_text", "Only UTF-8 text files are supported", "parameter")
            return success({"path": path, "content": value}, bytes=len(value.encode("utf-8")))

        if action != "write":
            return failure("invalid_action", "action must be read, write, or list", "parameter")
        if content is None:
            return failure("missing_content", "content is required for write", "parameter")
        encoded = content.encode("utf-8")
        if len(encoded) > self.settings.max_file_bytes:
            return failure("file_too_large", "content exceeds MCP_MAX_FILE_BYTES", "parameter")

        async def write_once() -> dict[str, Any]:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            await asyncio.to_thread(temporary.write_text, content, "utf-8")
            await asyncio.to_thread(temporary.replace, target)
            return {"path": path, "bytes": len(encoded)}

        report = await run_verified(
            write_once,
            lambda value: (
                target.exists()
                and target.read_text(encoding="utf-8") == content
                and value["bytes"] == len(encoded)
            ),
        )
        if not report.verified or report.value is None:
            return failure(
                "verification_failed",
                "Write could not be verified",
                "transient",
                retryable=True,
                attempts=_attempts_meta(report),
            )
        return success(report.value, verified=True, attempts=_attempts_meta(report))

    async def git_query(
        self,
        operation: Literal["status", "branch", "log", "diff"] = "status",
        repo_path: str = ".",
        limit: int = 10,
    ) -> ToolResult:
        if operation not in {"status", "branch", "log", "diff"}:
            return failure("invalid_operation", "Unsupported Git operation", "parameter")
        if not 1 <= limit <= 50:
            return failure("invalid_limit", "limit must be between 1 and 50", "parameter")
        try:
            repo = resolve_inside(self.settings.repository_root, repo_path)
        except SecurityViolation as exc:
            return failure("path_not_allowed", str(exc), "permission")
        if not repo.exists() or not repo.is_dir():
            return failure("repo_not_found", "Repository path does not exist", "parameter")

        commands = {
            "status": ["git", "status", "--short", "--branch"],
            "branch": ["git", "branch", "--show-current"],
            "log": ["git", "log", f"-{limit}", "--pretty=format:%h%x09%ad%x09%s", "--date=short"],
            "diff": ["git", "diff", "--stat"],
        }

        async def query_once() -> subprocess.CompletedProcess[str]:
            return await asyncio.to_thread(
                subprocess.run,
                commands[operation],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
                check=False,
            )

        report = await run_verified(
            query_once,
            lambda proc: proc.returncode == 0 and len(proc.stdout) <= 100_000,
        )
        if not report.verified or report.value is None:
            return failure(
                "git_query_failed",
                "Git command failed or its output could not be verified",
                "transient",
                retryable=True,
                attempts=_attempts_meta(report),
            )
        process = report.value
        return success(
            {"operation": operation, "output": process.stdout.strip()},
            verified=True,
            attempts=_attempts_meta(report),
        )

    async def ci_status(
        self,
        source: Literal["local", "github"] = "local",
        repository: str | None = None,
        limit: int = 5,
    ) -> ToolResult:
        if not 1 <= limit <= 20:
            return failure("invalid_limit", "limit must be between 1 and 20", "parameter")
        if source == "local":
            return self._local_ci_status()
        if source != "github":
            return failure("invalid_source", "source must be local or github", "parameter")
        if not repository or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            return failure(
                "invalid_repository",
                "repository must use the owner/name form",
                "parameter",
            )

        url = f"https://api.github.com/repos/{repository}/actions/runs?per_page={limit}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "personal-knowledge-mcp"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"

        def fetch() -> dict[str, Any]:
            request = urllib.request.Request(url, headers=headers)
            with self.urlopen(request, timeout=self.settings.command_timeout_seconds) as response:
                return json.load(response)

        try:
            payload = await asyncio.to_thread(fetch)
        except urllib.error.HTTPError as exc:
            category = "permission" if exc.code in {401, 403} else "transient"
            return failure(
                "github_http_error",
                f"GitHub API returned HTTP {exc.code}",
                category,
                retryable=exc.code >= 500,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return failure(
                "github_unreachable",
                str(exc),
                "transient",
                retryable=True,
            )

        runs = [
            {
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "branch": run.get("head_branch"),
                "url": run.get("html_url"),
                "created_at": run.get("created_at"),
            }
            for run in payload.get("workflow_runs", [])[:limit]
        ]
        return success({"source": "github", "repository": repository, "runs": runs})

    def _local_ci_status(self) -> ToolResult:
        workflow_root = self.settings.repository_root / ".github" / "workflows"
        workflows = (
            sorted(path.name for path in workflow_root.glob("*.y*ml"))
            if workflow_root.exists()
            else []
        )
        status_path = self.settings.repository_root / ".ci" / "status.json"
        status: dict[str, Any] | None = None
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                status = {"state": "invalid", "message": ".ci/status.json is not valid JSON"}
        return success(
            {
                "source": "local",
                "workflows": workflows,
                "last_recorded_status": status,
                "state": status.get("state", "unknown") if status else "unknown",
            }
        )

    async def run_command(
        self,
        command: list[str],
        cwd: str = ".",
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        if not command or not all(isinstance(item, str) and item for item in command):
            return failure(
                "invalid_command", "command must be a non-empty string list", "parameter"
            )
        executable = executable_name(command[0])
        if executable not in self.settings.allowed_commands:
            return failure(
                "command_not_allowed",
                f"'{executable}' is not in MCP_ALLOWED_COMMANDS",
                "permission",
                details={"allowed": list(self.settings.allowed_commands)},
            )
        try:
            run_cwd = resolve_inside(self.settings.workspace_root, cwd)
        except SecurityViolation as exc:
            return failure("path_not_allowed", str(exc), "permission")
        if not run_cwd.exists() or not run_cwd.is_dir():
            return failure("cwd_not_found", "cwd does not exist", "parameter")

        timeout = min(timeout_seconds or self.settings.command_timeout_seconds, 30.0)
        if timeout <= 0:
            return failure("invalid_timeout", "timeout_seconds must be positive", "parameter")
        actual_command = list(command)
        if executable in {"python", "python3"}:
            actual_command[0] = sys.executable

        try:
            process = await asyncio.to_thread(
                subprocess.run,
                actual_command,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired:
            return failure(
                "command_timeout",
                f"Command exceeded {timeout:.1f} seconds",
                "timeout",
                retryable=True,
            )
        except OSError as exc:
            return failure("command_failed_to_start", str(exc), "transient", retryable=True)

        output_limit = 20_000
        return success(
            {
                "command": command,
                "returncode": process.returncode,
                "stdout": process.stdout[:output_limit],
                "stderr": process.stderr[:output_limit],
            },
            output_truncated=len(process.stdout) > output_limit
            or len(process.stderr) > output_limit,
        )
