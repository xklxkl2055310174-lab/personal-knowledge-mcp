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
    command_output_bytes: int = 20_000
    allowed_commands: tuple[str, ...] = ("python", "python3", "pytest", "git", "rg")
    github_token: str | None = None
    sandbox_mode: str = "local"
    sandbox_image: str = "python:3.12-slim"
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None

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
            command_output_bytes=int(os.getenv("MCP_COMMAND_OUTPUT_BYTES", "20000")),
            allowed_commands=commands,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            sandbox_mode=os.getenv("MCP_SANDBOX", "local").strip().lower(),
            sandbox_image=os.getenv("MCP_SANDBOX_IMAGE", "python:3.12-slim").strip(),
            llm_provider=os.getenv("LLM_PROVIDER") or None,
            llm_model=os.getenv("LLM_MODEL") or None,
            llm_base_url=os.getenv("LLM_BASE_URL") or None,
            llm_api_key=os.getenv("LLM_API_KEY") or None,
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
