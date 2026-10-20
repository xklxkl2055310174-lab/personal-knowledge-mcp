"""A small, explicit verification loop for tool execution."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .contracts import ToolResult, failure

T = TypeVar("T")


@dataclass(slots=True)
class Attempt:
    number: int
    duration_ms: int
    outcome: str


@dataclass(slots=True)
class VerificationReport(Generic[T]):
    value: T | None
    verified: bool
    attempts: list[Attempt] = field(default_factory=list)


async def run_verified(
    operation: Callable[[], Awaitable[T]],
    verifier: Callable[[T], bool],
    *,
    max_attempts: int = 2,
    backoff_seconds: float = 0.05,
) -> VerificationReport[T]:
    """Run, verify, and retry only transient failures.

    Parameter and permission failures belong outside this function because repeating them
    cannot change the outcome. Timeouts and OS-level transient failures get one bounded retry.
    """

    attempts: list[Attempt] = []
    for number in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            value = await operation()
            verified = verifier(value)
            attempts.append(
                Attempt(
                    number,
                    int((time.perf_counter() - started) * 1000),
                    "verified" if verified else "verification_failed",
                )
            )
            if verified:
                return VerificationReport(value=value, verified=True, attempts=attempts)
        except (TimeoutError, OSError) as exc:
            attempts.append(
                Attempt(number, int((time.perf_counter() - started) * 1000), type(exc).__name__)
            )
        if number < max_attempts:
            await asyncio.sleep(backoff_seconds * number)
    return VerificationReport(value=None, verified=False, attempts=attempts)


def verification_failure(report: VerificationReport[object]) -> ToolResult:
    return failure(
        "verification_failed",
        "The operation did not pass verification after the allowed attempts.",
        "transient",
        retryable=True,
        attempts=[
            {"number": item.number, "duration_ms": item.duration_ms, "outcome": item.outcome}
            for item in report.attempts
        ],
    )
