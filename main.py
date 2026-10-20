"""PyCharm-friendly entry point for the Personal Knowledge MCP server.

Right-click this file and choose Run after selecting the project's .venv interpreter.
"""

from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"

# This repository uses the standard ``src`` package layout. Adding it here makes
# ``python main.py`` behave like the installed ``knowledge-mcp`` console command.
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

# Port 8000 is occupied on this Windows machine, so the PyCharm-friendly entry
# point uses 8001 unless the caller explicitly supplies a different port.
os.environ.setdefault("MCP_TRANSPORT", "streamable-http")
os.environ.setdefault("MCP_HOST", "127.0.0.1")
os.environ.setdefault("MCP_PORT", "8001")

run_server = import_module("knowledge_mcp.server").main


if __name__ == "__main__":
    run_server()
