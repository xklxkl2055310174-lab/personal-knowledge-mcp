"""Implementations behind the five MCP tools."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from .config import Settings
from .contracts import ToolResult, failure, success
from .index import NoteIndex
from .security import SecurityViolation, executable_name, resolve_inside
from .verification import (
    FailureClass,
    attempts_payload,
    run_verified,
    run_verified_sync,
)

SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


class ToolExecutionError(RuntimeError):
    def __init__(self, classification: FailureClass) -> None:
        super().__init__(classification.message)
        self.classification = classification


def _classify_tool_exception(exc: Exception) -> FailureClass:
    if isinstance(exc, ToolExecutionError):
        return exc.classification
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
        return FailureClass("timeout", "operation_timeout", True, str(exc))
    if isinstance(exc, OSError):
        return FailureClass("transient", "os_error", True, str(exc))
    return FailureClass("internal", "unexpected_error", False, type(exc).__name__)


def _minimal_environment() -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT")
    environment = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return environment


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _run_limited_process(
    command: list[str],
    cwd: str,
    timeout: float,
    output_limit: int,
    environment: dict[str, str],
) -> dict[str, Any]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        shell=False,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = threading.Event()

    def read_stream(name: str, stream: Any) -> None:
        while chunk := stream.read(4096):
            remaining = output_limit - len(buffers[name])
            if remaining > 0:
                buffers[name].extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated.set()
                _terminate_process_tree(process)
                return

    threads = [
        threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        process.wait(timeout=5)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=2)
        # Closing the inherited pipe handles matters on Windows: a completed child can
        # otherwise keep its working directory busy until Popen is garbage-collected.
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    return {
        "returncode": returncode,
        "stdout": buffers["stdout"].decode("utf-8", errors="replace"),
        "stderr": buffers["stderr"].decode("utf-8", errors="replace"),
        "output_truncated": truncated.is_set(),
    }


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
        self._file_locks: dict[str, asyncio.Lock] = {}
        self.note_index = NoteIndex(
            settings.knowledge_root / "notes",
            settings.runtime_root / "notes-index.sqlite3",
            settings.max_file_bytes,
        )

    def search_notes(self, query: str, top_k: int = 5) -> ToolResult:
        query = query.strip()
        if not query:
            return failure("empty_query", "query must not be empty", "parameter")
        if not 1 <= top_k <= 20:
            return failure("invalid_top_k", "top_k must be between 1 and 20", "parameter")

        notes_root = self.settings.knowledge_root / "notes"

        def search_once() -> tuple[list[dict[str, Any]], str | None]:
            return self.note_index.search(query, top_k)

        def verify(value: tuple[list[dict[str, Any]], str | None]) -> bool:
            for match in value[0]:
                target = resolve_inside(notes_root, match["path"], allow_root=False)
                if not target.is_file():
                    return False
                lines = target.read_text(encoding="utf-8").splitlines()
                start, end = match["start_line"], match["end_line"]
                if start < 1 or end > len(lines):
                    return False
                if "\n".join(lines[start - 1 : end]).strip() != match["content"].strip():
                    return False
            return True

        report = run_verified_sync(search_once, verify, max_attempts=2)
        if not report.verified or report.value is None:
            return failure(
                "search_verification_failed",
                "Search evidence could not be verified against the source files",
                "transient",
                retryable=True,
                attempts=attempts_payload(report),
            )
        selected, fallback = report.value
        documents = []
        for relative in dict.fromkeys(item["path"] for item in selected):
            text = (notes_root / relative).read_text(encoding="utf-8")
            documents.append({"path": relative, "content": text})
        scanned_files = self.note_index.status()["files"]
        return success(
            {"query": query, "matches": selected, "documents": documents},
            scanned_files=scanned_files,
            total_matches=len(selected),
            truncated=False,
            verified=True,
            attempts=attempts_payload(report),
            fallback=fallback,
        )

    async def file_access(
        self,
        action: Literal["list", "read", "write", "replace"],
        path: str = ".",
        content: str | None = None,
        mode: Literal["create", "overwrite"] = "overwrite",
        expected_sha256: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
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
                encoded = target.read_bytes()
                value = encoded.decode("utf-8")
            except UnicodeError:
                return failure("not_text", "Only UTF-8 text files are supported", "parameter")
            return success(
                {
                    "path": path,
                    "content": value,
                    "bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                },
                bytes=len(encoded),
            )

        if action not in {"write", "replace"}:
            return failure(
                "invalid_action",
                "action must be list, read, write, or replace",
                "parameter",
            )

        normalized_expected: str | None = None
        if expected_sha256 is not None:
            normalized_expected = expected_sha256.strip().lower()
            if not SHA256_RE.fullmatch(normalized_expected):
                return failure(
                    "invalid_expected_sha256",
                    "expected_sha256 must be exactly 64 hexadecimal characters",
                    "parameter",
                )

        if action == "write":
            if mode not in {"create", "overwrite"}:
                return failure("invalid_mode", "mode must be create or overwrite", "parameter")
            if content is None:
                return failure("missing_content", "content is required for write", "parameter")
            if mode == "create" and normalized_expected is not None:
                return failure(
                    "invalid_write_condition",
                    "expected_sha256 cannot be used with create mode",
                    "parameter",
                )
        else:
            if old_text is None or new_text is None:
                return failure(
                    "missing_replace_text",
                    "old_text and new_text are required for replace",
                    "parameter",
                )
            if old_text == "":
                return failure("empty_old_text", "old_text must not be empty", "parameter")

        lock_key = str(target)
        lock = self._file_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            if target.exists() and not target.is_file():
                return failure("not_a_file", "Path is not a file", "parameter")

            current_encoded: bytes | None = None
            current_text: str | None = None
            current_sha256: str | None = None
            if target.exists():
                if target.stat().st_size > self.settings.max_file_bytes:
                    return failure("file_too_large", "File exceeds MCP_MAX_FILE_BYTES", "parameter")
                try:
                    current_encoded = target.read_bytes()
                    current_text = current_encoded.decode("utf-8")
                except UnicodeError:
                    return failure("not_text", "Only UTF-8 text files are supported", "parameter")
                current_sha256 = hashlib.sha256(current_encoded).hexdigest()

            if action == "write" and mode == "create" and current_encoded is not None:
                return failure(
                    "file_exists",
                    "File already exists; use overwrite mode to replace it",
                    "conflict",
                )
            if action == "replace" and current_encoded is None:
                return failure("file_not_found", "File does not exist", "parameter")
            if normalized_expected is not None:
                if current_sha256 is None:
                    return failure(
                        "file_changed",
                        "File no longer exists; read it again before writing",
                        "conflict",
                        details={"expected_sha256": normalized_expected, "actual_sha256": None},
                    )
                if current_sha256 != normalized_expected:
                    return failure(
                        "file_changed",
                        "File content changed; read it again before writing",
                        "conflict",
                        details={
                            "expected_sha256": normalized_expected,
                            "actual_sha256": current_sha256,
                        },
                    )

            replacements = 0
            if action == "replace":
                assert current_text is not None
                assert old_text is not None
                assert new_text is not None
                replacements = current_text.count(old_text)
                if replacements == 0:
                    return failure(
                        "old_text_not_found",
                        "old_text was not found; read the latest file before replacing",
                        "conflict",
                    )
                if replacements > 1:
                    return failure(
                        "ambiguous_old_text",
                        "old_text occurs more than once; provide a larger unique block",
                        "conflict",
                        details={"occurrences": replacements},
                    )
                final_content = current_text.replace(old_text, new_text, 1)
            else:
                assert content is not None
                final_content = content

            final_encoded = final_content.encode("utf-8")
            if len(final_encoded) > self.settings.max_file_bytes:
                return failure("file_too_large", "content exceeds MCP_MAX_FILE_BYTES", "parameter")
            final_sha256 = hashlib.sha256(final_encoded).hexdigest()
            backup_id = uuid.uuid4().hex
            backup_root = self.settings.runtime_root / "backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_data = backup_root / f"{backup_id}.bin"
            backup_meta = backup_root / f"{backup_id}.json"
            if current_encoded is not None:
                backup_data.write_bytes(current_encoded)
            backup_meta.write_text(
                json.dumps(
                    {
                        "backup_id": backup_id,
                        "path": path,
                        "existed": current_encoded is not None,
                        "sha256": current_sha256,
                        "created_at": time.time(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            async def write_once() -> dict[str, Any]:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                try:
                    await asyncio.to_thread(temporary.write_bytes, final_encoded)
                    await asyncio.to_thread(temporary.replace, target)
                finally:
                    if temporary.exists():
                        temporary.unlink(missing_ok=True)
                result: dict[str, Any] = {
                    "path": path,
                    "bytes": len(final_encoded),
                    "sha256": final_sha256,
                    "backup_id": backup_id,
                }
                if current_sha256 is not None:
                    result["previous_sha256"] = current_sha256
                if action == "replace":
                    result["replacements"] = 1
                return result

            report = await run_verified(
                write_once,
                lambda value: (
                    target.exists()
                    and target.is_file()
                    and target.read_bytes() == final_encoded
                    and value["sha256"] == final_sha256
                ),
            )
            if not report.verified or report.value is None:
                rolled_back = False
                try:
                    if current_encoded is None:
                        target.unlink(missing_ok=True)
                    else:
                        rollback_temp = target.with_name(f".{target.name}.{backup_id}.rollback")
                        rollback_temp.write_bytes(current_encoded)
                        rollback_temp.replace(target)
                    rolled_back = True
                except OSError:
                    rolled_back = False
                return failure(
                    "verification_failed",
                    "Write could not be verified",
                    "transient",
                    retryable=True,
                    attempts=attempts_payload(report),
                    backup_id=backup_id,
                    rolled_back=rolled_back,
                )
            return success(
                report.value,
                verified=True,
                attempts=attempts_payload(report),
                backup_id=backup_id,
            )

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
            process = await asyncio.to_thread(
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
            if process.returncode:
                message = (process.stderr or process.stdout).strip()[:500]
                if "not a git repository" in message.casefold():
                    classified = FailureClass("parameter", "not_a_git_repository", False, message)
                elif "unknown option" in message.casefold() or "usage:" in message.casefold():
                    classified = FailureClass("parameter", "git_invalid_argument", False, message)
                else:
                    classified = FailureClass(
                        "transient", "git_command_failed", True, message or "Git command failed"
                    )
                raise ToolExecutionError(classified)
            return process

        report = await run_verified(
            query_once,
            lambda proc: len(proc.stdout.encode("utf-8")) <= 100_000,
            classifier=_classify_tool_exception,
            deadline_seconds=max(1.0, self.settings.command_timeout_seconds * 2.2),
        )
        if not report.verified or report.value is None:
            classified = report.failure or FailureClass(
                "transient", "git_query_failed", True, "Git query failed"
            )
            return failure(
                classified.code,
                classified.message,
                classified.category,
                retryable=classified.retryable,
                attempts=attempts_payload(report),
            )
        process = report.value
        raw = process.stdout.strip()
        data: dict[str, Any] = {"operation": operation, "output": raw}
        if operation == "status":
            lines = raw.splitlines()
            data["branch"] = (
                lines[0].removeprefix("## ").split("...")[0]
                if lines and lines[0].startswith("## ")
                else ""
            )
            data["files"] = [
                {"status": line[:2], "path": line[3:]}
                for line in lines
                if line and not line.startswith("## ") and len(line) >= 4
            ]
        elif operation == "branch":
            data["branch"] = raw
        elif operation == "log":
            data["commits"] = [
                {"hash": parts[0], "date": parts[1], "subject": parts[2]}
                for line in raw.splitlines()
                if len(parts := line.split("\t", 2)) == 3
            ]
        elif operation == "diff":
            data["files_changed"] = [line.strip() for line in raw.splitlines() if "|" in line]
        return success(
            data,
            verified=True,
            attempts=attempts_payload(report),
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
            try:
                with self.urlopen(
                    request, timeout=self.settings.command_timeout_seconds
                ) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
                reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
                if exc.code == 404:
                    classified = FailureClass(
                        "parameter", "github_repository_not_found", False, "repository not found"
                    )
                elif exc.code == 401:
                    classified = FailureClass(
                        "permission", "github_unauthorized", False, "GitHub token is invalid"
                    )
                elif exc.code == 429 or (exc.code == 403 and remaining == "0"):
                    classified = FailureClass(
                        "transient",
                        "github_rate_limited",
                        True,
                        f"GitHub rate limit reached; reset={reset or 'unknown'}",
                    )
                elif exc.code == 403:
                    classified = FailureClass(
                        "permission", "github_forbidden", False, "GitHub access is forbidden"
                    )
                elif exc.code >= 500:
                    classified = FailureClass(
                        "transient", "github_server_error", True, f"GitHub HTTP {exc.code}"
                    )
                else:
                    classified = FailureClass(
                        "parameter", "github_http_error", False, f"GitHub HTTP {exc.code}"
                    )
                raise ToolExecutionError(classified) from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ToolExecutionError(
                    FailureClass("transient", "github_unreachable", True, str(exc))
                ) from None

        def valid_payload(payload: dict[str, Any]) -> bool:
            if not isinstance(payload.get("workflow_runs"), list):
                return False
            allowed = {"queued", "in_progress", "completed", "requested", "waiting", "pending"}
            return all(
                isinstance(run, dict) and str(run.get("status")) in allowed
                for run in payload["workflow_runs"]
            )

        report = await run_verified(
            lambda: asyncio.to_thread(fetch),
            valid_payload,
            classifier=_classify_tool_exception,
            max_attempts=3,
            deadline_seconds=max(2.0, self.settings.command_timeout_seconds * 3.2),
        )
        if not report.verified or report.value is None:
            classified = report.failure or FailureClass(
                "transient", "github_unreachable", True, "GitHub Actions is unavailable"
            )
            if classified.category == "transient":
                local = self._local_ci_status()
                if local.ok:
                    return success(
                        {
                            **local.data,
                            "requested_source": "github",
                            "repository": repository,
                        },
                        verified=True,
                        stale=True,
                        fallback="local",
                        fallback_reason=classified.code,
                        attempts=attempts_payload(report),
                    )
            return failure(
                classified.code,
                classified.message,
                classified.category,
                retryable=classified.retryable,
                attempts=attempts_payload(report),
            )

        payload = report.value
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
        return success(
            {"source": "github", "repository": repository, "runs": runs},
            verified=True,
            attempts=attempts_payload(report),
            fallback=None,
            stale=False,
        )

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
        result = success(
            {
                "source": "local",
                "workflows": workflows,
                "last_recorded_status": status,
                "state": status.get("state", "unknown") if status else "unknown",
            },
            verified=True,
            attempts=[{"number": 1, "duration_ms": 0, "outcome": "verified"}],
        )
        allowed_states = {"unknown", "passed", "failed", "pending", "running", "invalid"}
        if result.data["state"] not in allowed_states:
            return failure(
                "invalid_ci_state",
                "Local CI state is not recognized",
                "parameter",
                details={"state": result.data["state"]},
                attempts=result.meta["attempts"],
            )
        return result

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
        for argument in command[1:]:
            candidate = Path(argument)
            if candidate.is_absolute() or ".." in candidate.parts:
                return failure(
                    "argument_path_not_allowed",
                    "Command arguments cannot contain absolute paths or path traversal",
                    "permission",
                )

        actual_command = list(command)
        if executable in {"python", "python3"}:
            if len(command) < 2 or command[1].startswith("-"):
                return failure(
                    "python_argument_not_allowed",
                    "Python may only run a workspace script; -c and -m are disabled",
                    "permission",
                )
            try:
                script = resolve_inside(run_cwd, command[1], allow_root=False)
                script.relative_to(self.settings.workspace_root.resolve())
            except (SecurityViolation, ValueError) as exc:
                return failure("script_not_allowed", str(exc), "permission")
            if not script.is_file() or script.suffix.lower() != ".py":
                return failure(
                    "script_not_found",
                    "Python target must be an existing .py file inside the workspace",
                    "parameter",
                )
            actual_command[0] = sys.executable
            actual_command[1] = str(script)
        if executable == "git" and (
            len(command) < 2
            or command[1]
            not in {
                "status",
                "log",
                "diff",
                "show",
                "branch",
                "rev-parse",
            }
        ):
            return failure(
                "git_subcommand_not_allowed",
                "git only allows status/log/diff/show/branch/rev-parse",
                "permission",
            )

        if self.settings.sandbox_mode not in {"local", "docker"}:
            return failure(
                "invalid_sandbox_mode",
                "MCP_SANDBOX must be local or docker",
                "parameter",
            )
        sandbox = self.settings.sandbox_mode
        if sandbox == "docker":
            if not shutil.which("docker"):
                return failure(
                    "docker_unavailable",
                    "Docker sandbox was requested but docker is not installed",
                    "transient",
                    retryable=False,
                )
            relative_cwd = run_cwd.relative_to(self.settings.workspace_root.resolve()).as_posix()
            container_cwd = "/workspace" if relative_cwd == "." else f"/workspace/{relative_cwd}"
            container_command = list(command)
            if executable in {"python", "python3"}:
                script_relative = (
                    Path(actual_command[1])
                    .relative_to(self.settings.workspace_root.resolve())
                    .as_posix()
                )
                container_command = ["python", f"/workspace/{script_relative}", *command[2:]]
            actual_command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--pids-limit",
                "64",
                "--user",
                "65534:65534",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=32m",
                "-v",
                f"{self.settings.workspace_root.resolve()}:/workspace:rw",
                "-w",
                container_cwd,
                self.settings.sandbox_image,
                *container_command,
            ]

        async def execute_once() -> dict[str, Any]:
            return await asyncio.to_thread(
                _run_limited_process,
                actual_command,
                str(run_cwd),
                timeout,
                self.settings.command_output_bytes,
                _minimal_environment(),
            )

        report = await run_verified(
            execute_once,
            lambda value: (
                isinstance(value.get("returncode"), int)
                and isinstance(value.get("stdout"), str)
                and isinstance(value.get("stderr"), str)
            ),
            classifier=_classify_tool_exception,
            max_attempts=1,
            deadline_seconds=timeout + 2,
        )
        if not report.verified or report.value is None:
            classified = report.failure or FailureClass(
                "transient", "command_failed_to_start", False, "Command failed"
            )
            code = "command_timeout" if classified.category == "timeout" else classified.code
            return failure(
                code,
                (
                    f"Command exceeded {timeout:.1f} seconds"
                    if classified.category == "timeout"
                    else classified.message
                ),
                classified.category,
                retryable=False,
                attempts=attempts_payload(report),
            )
        process = report.value
        return success(
            {
                "command": command,
                "returncode": process["returncode"],
                "exit_code": process["returncode"],
                "stdout": process["stdout"],
                "stderr": process["stderr"],
                "sandbox": sandbox,
            },
            verified=True,
            attempts=attempts_payload(report),
            output_truncated=process["output_truncated"],
        )
