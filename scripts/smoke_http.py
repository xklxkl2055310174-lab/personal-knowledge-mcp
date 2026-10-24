"""Call every tool through a running Streamable HTTP MCP endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp import Client


def structured(result: Any) -> dict[str, Any]:
    payload = result.structured_content
    if not isinstance(payload, dict):
        raise AssertionError("Tool did not return structured content")
    if not payload.get("ok"):
        raise AssertionError(payload)
    return payload


async def run(url: str) -> None:
    async with Client(url) as client:
        discovered = {tool.name for tool in (await client.list_tools()).tools}
        expected = {"search_notes", "file_access", "git_query", "ci_status", "run_command"}
        if discovered != expected:
            raise AssertionError({"expected": sorted(expected), "actual": sorted(discovered)})

        results = {
            "search_notes": structured(
                await client.call_tool("search_notes", {"query": "MCP Agent", "top_k": 3})
            ),
            "file_access.write": structured(
                await client.call_tool(
                    "file_access",
                    {"action": "write", "path": "smoke/hello.txt", "content": "MCP smoke OK"},
                )
            ),
            "file_access.read": structured(
                await client.call_tool("file_access", {"action": "read", "path": "smoke/hello.txt"})
            ),
            "git_query": structured(await client.call_tool("git_query", {"operation": "status"})),
            "ci_status": structured(await client.call_tool("ci_status", {"source": "local"})),
            "run_command": structured(
                await client.call_tool(
                    "run_command",
                    {
                        "command": ["python", "safe-command-demo.py"],
                        "cwd": "examples",
                    },
                )
            ),
        }
        print(
            json.dumps(
                {
                    "status": "passed",
                    "endpoint": url,
                    "tools": sorted(discovered),
                    "checks": sorted(results),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == "__main__":
    main()
