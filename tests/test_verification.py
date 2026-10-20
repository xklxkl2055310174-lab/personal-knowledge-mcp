from __future__ import annotations

import pytest

from knowledge_mcp.verification import run_verified


@pytest.mark.asyncio
async def test_verification_retries_transient_failure() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return "verified"

    report = await run_verified(operation, lambda value: value == "verified")

    assert report.verified
    assert calls == 2
    assert [attempt.outcome for attempt in report.attempts] == ["TimeoutError", "verified"]
