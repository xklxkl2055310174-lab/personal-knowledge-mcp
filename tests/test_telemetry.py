from __future__ import annotations

from knowledge_mcp.contracts import failure, success
from knowledge_mcp.telemetry import TraceStore


def test_tool_call_log_redacts_and_aggregates(tmp_path: object) -> None:
    store = TraceStore(tmp_path / "traces.sqlite3")  # type: ignore[operator]
    store.record_tool_call(
        "search_notes",
        {"query": "hello", "api_key": "secret", "content": "private"},
        success({"matches": []}, attempts=[{"number": 1, "duration_ms": 2, "outcome": "verified"}]),
        3,
        "trace-ok",
        caller="agent",
    )
    store.record_tool_call(
        "file_access",
        {"path": "../outside"},
        failure("path_not_allowed", "no", "permission"),
        1,
        "trace-fail",
        caller="frontend",
    )
    rows = store.list_tool_calls(limit=10)
    serialized = str(rows)
    assert "secret" not in serialized and "private" not in serialized
    assert {"agent", "frontend"} == {row["caller"] for row in rows}
    distribution = store.failure_distribution()
    assert any(
        row["tool"] == "file_access" and row["category"] == "permission" for row in distribution
    )


def test_task_trace_round_trip(tmp_path: object) -> None:
    store = TraceStore(tmp_path / "traces.sqlite3")  # type: ignore[operator]
    task_id = store.create_task("问题", "delegated", "relevant")
    store.update_task(task_id, status="completed", answer="回答")
    task = store.get_task(task_id)
    assert task and task["answer"] == "回答"
    assert task["status"] == "completed"


def test_conversation_memory_persists_and_links_task(tmp_path: object) -> None:
    store = TraceStore(tmp_path / "traces.sqlite3")  # type: ignore[operator]
    conversation_id = "conversation_test_01"
    store.append_conversation_message(conversation_id, "user", "第一问")
    store.append_conversation_message(conversation_id, "assistant", "第一答")
    task_id = store.create_task("第二问", "delegated", "relevant", conversation_id)

    messages = store.list_conversation_messages(conversation_id)
    task = store.get_task(task_id)

    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert [item["content"] for item in messages] == ["第一问", "第一答"]
    assert task and task["conversation_id"] == conversation_id
    assert store.clear_conversation(conversation_id) is True
    assert store.list_conversation_messages(conversation_id) == []
