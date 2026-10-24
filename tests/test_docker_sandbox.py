from __future__ import annotations

import os
import shutil
from dataclasses import replace

import pytest

from knowledge_mcp.config import Settings
from knowledge_mcp.services import KnowledgeServices


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_sandbox_runs_without_network_or_root(settings: Settings) -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker is not installed")
    script = settings.workspace_root / "sandbox.py"
    script.write_text(
        "import os, socket\n"
        "print('uid', getattr(os, 'getuid', lambda: -1)())\n"
        "try:\n socket.create_connection(('example.com', 80), 0.2)\n"
        "except OSError:\n print('network-blocked')\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        settings.workspace_root.chmod(0o777)
        script.chmod(0o644)
    docker_settings = replace(settings, sandbox_mode="docker")
    result = await KnowledgeServices(docker_settings).run_command(
        ["python", "sandbox.py"], timeout_seconds=20
    )
    assert result.ok
    assert result.data["sandbox"] == "docker"
    assert "uid 0" not in result.data["stdout"]
    assert "network-blocked" in result.data["stdout"]
