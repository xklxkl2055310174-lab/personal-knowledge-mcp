"""Shared verification loop: execute, validate, classify, retry or fall back."""

from __future__ import annotations

import asyncio
import random
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .contracts import ErrorCategory, ToolResult, failure

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FailureClass:
    category: ErrorCategory
    code: str
    retryable: bool
    message: str


@dataclass(slots=True)
class Attempt:
    number: int
    duration_ms: int
    outcome: str
    category: ErrorCategory | None = None
    code: str | None = None


@dataclass(slots=True)
class VerificationReport(Generic[T]):
    value: T | None
    verified: bool
    attempts: list[Attempt] = field(default_factory=list)
    failure: FailureClass | None = None
    fallback_used: bool = False


Classifier = Callable[[Exception], FailureClass]


def classify_exception(exc: Exception) -> FailureClass:
    """Default exception classifier shared by all tools."""

    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, subprocess.TimeoutExpired)):
        return FailureClass("timeout", "operation_timeout", True, str(exc) or "operation timed out")
    if isinstance(exc, (PermissionError,)):
        return FailureClass("permission", "permission_denied", False, str(exc))
    if isinstance(exc, (ValueError, TypeError)):
        return FailureClass("parameter", "invalid_parameter", False, str(exc))
    if isinstance(exc, OSError):
        return FailureClass("transient", "os_error", True, str(exc))
    return FailureClass("internal", "unexpected_error", False, type(exc).__name__)


async def run_verified(
    operation: Callable[[], Awaitable[T]],
    verifier: Callable[[T], bool],
    *,
    classifier: Classifier = classify_exception,
    max_attempts: int = 2,
    backoff_seconds: float = 0.05,
    jitter_seconds: float = 0.025,
    deadline_seconds: float = 10.0,
    fallback: Callable[[], Awaitable[T]] | None = None,
    random_fn: Callable[[], float] = random.random,
) -> VerificationReport[T]:
    """Execute and verify with bounded exponential backoff and optional fallback.

    Only failures classified as timeout/transient and marked retryable are repeated.
    The total deadline includes attempts and sleep. A fallback is attempted once after
    the primary path is exhausted and is always visible in the returned report.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    started_all = time.monotonic()
    attempts: list[Attempt] = []
    last_failure: FailureClass | None = None

    for number in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            remaining = deadline_seconds - (time.monotonic() - started_all)
            if remaining <= 0:
                raise TimeoutError("verification deadline exceeded")
            value = await asyncio.wait_for(operation(), timeout=remaining)
            verified = verifier(value)
            attempts.append(
                Attempt(
                    number,
                    int((time.perf_counter() - started) * 1000),
                    "verified" if verified else "verification_failed",
                    None if verified else "transient",
                    None if verified else "verification_failed",
                )
            )
            if verified:
                return VerificationReport(value, True, attempts)
            last_failure = FailureClass(
                "transient",
                "verification_failed",
                True,
                "operation returned a value that failed verification",
            )
        except Exception as exc:  # noqa: BLE001 - classification is this function's purpose
            last_failure = classifier(exc)
            attempts.append(
                Attempt(
                    number,
                    int((time.perf_counter() - started) * 1000),
                    type(exc).__name__,
                    last_failure.category,
                    last_failure.code,
                )
            )

        if (
            number >= max_attempts
            or last_failure is None
            or not last_failure.retryable
            or last_failure.category not in {"timeout", "transient"}
        ):
            break
        delay = backoff_seconds * (2 ** (number - 1)) + jitter_seconds * random_fn()
        remaining = deadline_seconds - (time.monotonic() - started_all)
        if remaining <= delay:
            break
        await asyncio.sleep(delay)

    if fallback is not None:
        started = time.perf_counter()
        try:
            remaining = deadline_seconds - (time.monotonic() - started_all)
            if remaining <= 0:
                raise TimeoutError("verification deadline exceeded before fallback")
            value = await asyncio.wait_for(fallback(), timeout=remaining)
            verified = verifier(value)
            attempts.append(
                Attempt(
                    len(attempts) + 1,
                    int((time.perf_counter() - started) * 1000),
                    "fallback_verified" if verified else "fallback_failed",
                )
            )
            if verified:
                return VerificationReport(value, True, attempts, fallback_used=True)
        except Exception as exc:  # noqa: BLE001
            last_failure = classifier(exc)
            attempts.append(
                Attempt(
                    len(attempts) + 1,
                    int((time.perf_counter() - started) * 1000),
                    f"fallback_{type(exc).__name__}",
                    last_failure.category,
                    last_failure.code,
                )
            )

    return VerificationReport(None, False, attempts, last_failure)


def run_verified_sync(
    operation: Callable[[], T],
    verifier: Callable[[T], bool],
    *,
    classifier: Classifier = classify_exception,
    max_attempts: int = 2,
    backoff_seconds: float = 0.05,
    jitter_seconds: float = 0.025,
    deadline_seconds: float = 10.0,
    fallback: Callable[[], T] | None = None,
    random_fn: Callable[[], float] = random.random,
) -> VerificationReport[T]:
    """Synchronous counterpart used by CPU/local-file tools."""

    started_all = time.monotonic()
    attempts: list[Attempt] = []
    last_failure: FailureClass | None = None
    for number in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            if time.monotonic() - started_all >= deadline_seconds:
                raise TimeoutError("verification deadline exceeded")
            value = operation()
            verified = verifier(value)
            attempts.append(
                Attempt(
                    number,
                    int((time.perf_counter() - started) * 1000),
                    "verified" if verified else "verification_failed",
                    None if verified else "transient",
                    None if verified else "verification_failed",
                )
            )
            if verified:
                return VerificationReport(value, True, attempts)
            last_failure = FailureClass(
                "transient", "verification_failed", True, "verification failed"
            )
        except Exception as exc:  # noqa: BLE001
            last_failure = classifier(exc)
            attempts.append(
                Attempt(
                    number,
                    int((time.perf_counter() - started) * 1000),
                    type(exc).__name__,
                    last_failure.category,
                    last_failure.code,
                )
            )
        if (
            number >= max_attempts
            or last_failure is None
            or not last_failure.retryable
            or last_failure.category not in {"timeout", "transient"}
        ):
            break
        delay = backoff_seconds * (2 ** (number - 1)) + jitter_seconds * random_fn()
        if time.monotonic() - started_all + delay >= deadline_seconds:
            break
        time.sleep(delay)
    if fallback is not None:
        started = time.perf_counter()
        try:
            value = fallback()
            verified = verifier(value)
            attempts.append(
                Attempt(
                    len(attempts) + 1,
                    int((time.perf_counter() - started) * 1000),
                    "fallback_verified" if verified else "fallback_failed",
                )
            )
            if verified:
                return VerificationReport(value, True, attempts, fallback_used=True)
        except Exception as exc:  # noqa: BLE001
            last_failure = classifier(exc)
    return VerificationReport(None, False, attempts, last_failure)


def attempts_payload(report: VerificationReport[object]) -> list[dict[str, object]]:
    return [
        {
            "number": item.number,
            "duration_ms": item.duration_ms,
            "outcome": item.outcome,
            "category": item.category,
            "code": item.code,
        }
        for item in report.attempts
    ]


def verification_failure(report: VerificationReport[object]) -> ToolResult:
    classified = report.failure or FailureClass(
        "transient", "verification_failed", True, "verification failed"
    )
    return failure(
        classified.code,
        classified.message,
        classified.category,
        retryable=classified.retryable,
        attempts=attempts_payload(report),
        fallback=report.fallback_used,
    )
