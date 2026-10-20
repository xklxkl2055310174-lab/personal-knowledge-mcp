from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_mcp.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    value = Settings(
        project_root=tmp_path,
        knowledge_root=tmp_path / "knowledge",
        workspace_root=tmp_path / "workspace",
        repository_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        command_timeout_seconds=2,
    )
    value.ensure_directories()
    value.repository_root.mkdir()
    return value
