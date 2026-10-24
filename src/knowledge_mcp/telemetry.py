"""SQLite-backed task traces and privacy-safe MCP tool call audit."""

from __future__ import annotations

import contextvars
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .contracts import ToolResult

_caller = contextvars.ContextVar("knowledge_mcp_caller", default="external_host")
_task = contextvars.ContextVar("knowledge_mcp_task", default=None)
SECRET_KEYS = {"api_key", "token", "authorization", "password", "secret", "content", "new_text"}


@contextmanager
def caller_scope(value: str) -> Iterator[None]:
    token = _caller.set(value)
    try:
        yield
    finally:
        _caller.reset(token)


def current_caller() -> str:
    return _caller.get()


def current_task_id() -> str | None:
    return _task.get()


@contextmanager
def trace_scope(caller: str, task_id: str | None) -> Iterator[None]:
    caller_token = _caller.set(caller)
    task_token = _task.set(task_id)
    try:
        yield
    finally:
        _task.reset(task_token)
        _caller.reset(caller_token)


def _sanitize(value: Any, key: str = "") -> Any:
    if key.casefold() in SECRET_KEYS or any(part in key.casefold() for part in ("token", "key")):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:20]]
    if isinstance(value, str):
        return value[:240] + ("…" if len(value) > 240 else "")
    return value


class TraceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    query TEXT NOT NULL, mode TEXT NOT NULL, strategy TEXT NOT NULL,
                    status TEXT NOT NULL, answer TEXT, metrics_json TEXT NOT NULL DEFAULT '{}',
                    error_json TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    name TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    status TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, step_id INTEGER,
                    created_at TEXT NOT NULL, tool TEXT NOT NULL, arguments_json TEXT NOT NULL,
                    caller TEXT NOT NULL, ok INTEGER NOT NULL, error_category TEXT,
                    error_code TEXT, attempts INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
                    trace_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tool_call_id INTEGER NOT NULL,
                    number INTEGER NOT NULL, duration_ms INTEGER NOT NULL, outcome TEXT NOT NULL,
                    category TEXT, code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tool_calls_filter
                    ON tool_calls(tool, error_category, caller, created_at);
                CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id);
                """
            )

    def record_tool_call(
        self,
        tool: str,
        arguments: dict[str, Any],
        result: ToolResult,
        duration_ms: int,
        trace_id: str,
        *,
        task_id: str | None = None,
        step_id: int | None = None,
        caller: str | None = None,
    ) -> int:
        attempts = result.meta.get("attempts") or []
        category = result.error.category if result.error else None
        code = result.error.code if result.error else None
        now = datetime.now(UTC).isoformat()
        with self._db() as db:
            cursor = db.execute(
                """INSERT INTO tool_calls(
                       task_id, step_id, created_at, tool, arguments_json, caller, ok,
                       error_category, error_code, attempts, duration_ms, trace_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    step_id,
                    now,
                    tool,
                    json.dumps(_sanitize(arguments), ensure_ascii=False),
                    caller or current_caller(),
                    int(result.ok),
                    category,
                    code,
                    max(1, len(attempts)),
                    duration_ms,
                    trace_id,
                ),
            )
            call_id = int(cursor.lastrowid)
            for item in attempts:
                db.execute(
                    """INSERT INTO attempts(
                           tool_call_id, number, duration_ms, outcome, category, code)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        call_id,
                        item.get("number", 1),
                        item.get("duration_ms", 0),
                        item.get("outcome", "unknown"),
                        item.get("category"),
                        item.get("code"),
                    ),
                )
        return call_id

    def list_tool_calls(
        self,
        *,
        tool: str | None = None,
        category: str | None = None,
        caller: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (("tool", tool), ("error_category", category), ("caller", caller)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        if since:
            clauses.append("created_at >= ?")
            values.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.extend((min(limit, 500), max(offset, 0)))
        with self._db() as db:
            rows = db.execute(
                f"""SELECT * FROM tool_calls {where}
                    ORDER BY id DESC LIMIT ? OFFSET ?""",
                values,
            )
            return [dict(row) for row in rows]

    def failure_distribution(self, hours: int = 24 * 30) -> list[dict[str, Any]]:
        since = (datetime.now(UTC) - timedelta(hours=max(1, hours))).isoformat()
        with self._db() as db:
            rows = db.execute(
                """SELECT tool, COALESCE(error_category, 'success') AS category, COUNT(*) AS count
                   FROM tool_calls WHERE created_at >= ?
                   GROUP BY tool, COALESCE(error_category, 'success')
                   ORDER BY tool, category""",
                (since,),
            )
            return [dict(row) for row in rows]

    def create_task(self, query: str, mode: str, strategy: str) -> str:
        task_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self._db() as db:
            db.execute(
                """INSERT INTO tasks(id, created_at, updated_at, query, mode, strategy, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'queued')""",
                (task_id, now, now, query, mode, strategy),
            )
        return task_id

    def record_step(
        self,
        task_id: str,
        name: str,
        duration_ms: int,
        detail: dict[str, Any],
        status: str = "completed",
    ) -> int:
        now = datetime.now(UTC)
        started = now - timedelta(milliseconds=max(0, duration_ms))
        with self._db() as db:
            cursor = db.execute(
                """INSERT INTO steps(
                       task_id, name, started_at, finished_at, status, detail_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    name,
                    started.isoformat(),
                    now.isoformat(),
                    status,
                    json.dumps(_sanitize(detail), ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def update_task(self, task_id: str, **fields: Any) -> None:
        allowed = {"status", "answer", "metrics_json", "error_json", "cancel_requested"}
        selected = {key: value for key, value in fields.items() if key in allowed}
        if not selected:
            return
        selected["updated_at"] = datetime.now(UTC).isoformat()
        assignments = ", ".join(f"{key} = ?" for key in selected)
        with self._db() as db:
            db.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?",
                (*selected.values(), task_id),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not task:
                return None
            data = dict(task)
            data["steps"] = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM steps WHERE task_id = ? ORDER BY id", (task_id,)
                )
            ]
            data["tool_calls"] = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM tool_calls WHERE task_id = ? ORDER BY id", (task_id,)
                )
            ]
            return data

    def list_tasks(self, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (min(limit, 100), max(offset, 0)),
                )
            ]
