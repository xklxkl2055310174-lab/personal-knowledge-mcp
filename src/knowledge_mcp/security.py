"""Path and process-safety primitives."""

from __future__ import annotations

import os
from pathlib import Path


class SecurityViolation(ValueError):
    """Raised when input attempts to escape an allowed boundary."""


def resolve_inside(root: Path, relative_path: str, *, allow_root: bool = True) -> Path:
    if not relative_path or relative_path == ".":
        if allow_root:
            return root.resolve()
        raise SecurityViolation("A non-empty relative path is required")

    candidate_input = Path(relative_path)
    if candidate_input.is_absolute():
        raise SecurityViolation("Absolute paths are not allowed")

    root_resolved = root.resolve()
    candidate = (root_resolved / candidate_input).resolve()
    try:
        inside = os.path.commonpath((str(root_resolved), str(candidate))) == str(root_resolved)
    except ValueError as exc:
        raise SecurityViolation("Path is on a different drive") from exc
    if not inside:
        raise SecurityViolation("Path escapes the configured root")
    return candidate


def executable_name(command: str) -> str:
    name = Path(command).name.lower()
    return name[:-4] if name.endswith(".exe") else name
