from __future__ import annotations

import subprocess

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


@pytest.mark.asyncio
async def test_verification_handles_subprocess_timeout_and_fallback() -> None:
    async def operation() -> str:
        raise subprocess.TimeoutExpired(["git"], 0.1)

    async def fallback() -> str:
        return "local"

    report = await run_verified(
        operation,
        lambda value: value == "local",
        max_attempts=1,
        fallback=fallback,
        random_fn=lambda: 0,
    )
    assert report.verified
    assert report.fallback_used
    assert report.attempts[0].category == "timeout"


@pytest.mark.asyncio
async def test_parameter_failure_is_not_retried() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("bad")

    report = await run_verified(operation, lambda _: True, max_attempts=3)
    assert not report.verified
    assert calls == 1
    assert report.failure and report.failure.category == "parameter"
