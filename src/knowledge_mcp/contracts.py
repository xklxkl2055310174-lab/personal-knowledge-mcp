"""Stable result envelopes shared by every MCP tool."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ErrorCategory = Literal[
    "parameter", "permission", "conflict", "timeout", "transient", "internal"
]


class ToolErrorInfo(BaseModel):
    code: str
    message: str
    category: ErrorCategory
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: ToolErrorInfo | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


def success(data: Any, **meta: Any) -> ToolResult:
    return ToolResult(ok=True, data=data, meta=meta)


def failure(
    code: str,
    message: str,
    category: ErrorCategory,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    **meta: Any,
) -> ToolResult:
    return ToolResult(
        ok=False,
        error=ToolErrorInfo(
            code=code,
            message=message,
            category=category,
            retryable=retryable,
            details=details or {},
        ),
        meta=meta,
    )
