"""Configuration loaded from environment variables.

The defaults deliberately keep writable knowledge and command execution in dedicated
directories.  The Git/CI tools can inspect the project repository, but they never write it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _path_from_env(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    knowledge_root: Path
    workspace_root: Path
    repository_root: Path
    runtime_root: Path
    max_file_bytes: int = 1_000_000
    command_timeout_seconds: float = 10.0
    allowed_commands: tuple[str, ...] = ("python", "python3", "pytest", "git", "rg")
    github_token: str | None = None

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        project = (project_root or _project_root()).resolve()
        commands = tuple(
            item.strip().lower()
            for item in os.getenv("MCP_ALLOWED_COMMANDS", "python,python3,pytest,git,rg").split(",")
            if item.strip()
        )
        settings = cls(
            project_root=project,
            knowledge_root=_path_from_env("MCP_KNOWLEDGE_ROOT", project / "knowledge"),
            workspace_root=_path_from_env("MCP_WORKSPACE_ROOT", project / "workspace"),
            repository_root=_path_from_env("MCP_REPOSITORY_ROOT", project),
            runtime_root=_path_from_env("MCP_RUNTIME_ROOT", project / ".runtime"),
            max_file_bytes=int(os.getenv("MCP_MAX_FILE_BYTES", "1000000")),
            command_timeout_seconds=float(os.getenv("MCP_COMMAND_TIMEOUT_SECONDS", "10")),
            allowed_commands=commands,
            github_token=os.getenv("GITHUB_TOKEN") or None,
        )
        settings.ensure_directories()
        return settings

    def ensure_directories(self) -> None:
        for directory in (
            self.knowledge_root / "notes",
            self.workspace_root,
            self.runtime_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
