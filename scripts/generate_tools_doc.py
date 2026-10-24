"""Generate docs/TOOLS.md from the live MCP tools/list response."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client

from knowledge_mcp.config import Settings
from knowledge_mcp.server import create_server

ERRORS = {
    "search_notes": "empty_query/invalid_top_k(parameter); search_verification_failed(transient)",
    "file_access": (
        "parameter; path_not_allowed(permission); file_changed/file_exists(conflict); "
        "verification_failed(transient)"
    ),
    "git_query": "parameter/permission; operation_timeout(timeout); git_command_failed(transient)",
    "ci_status": (
        "parameter/permission; github_rate_limited/github_server_error/"
        "github_unreachable(transient)"
    ),
    "run_command": (
        "parameter/permission; command_timeout(timeout); command_failed_to_start(transient)"
    ),
}
RETRY = {
    "search_notes": "仅 transient",
    "file_access": "仅 transient；写入自带校验/回滚",
    "git_query": "仅 timeout/transient",
    "ci_status": "仅 timeout/transient；可回退本地状态",
    "run_command": "否（命令不假定幂等）",
}


def schema_type(schema: dict[str, Any]) -> str:
    if "enum" in schema:
        return " \\| ".join(map(str, schema["enum"]))
    if "anyOf" in schema:
        return " / ".join(item.get("type", "?") for item in schema["anyOf"])
    return str(schema.get("type", "any"))


async def render() -> str:
    with tempfile.TemporaryDirectory(prefix="knowledge-mcp-tools-") as directory:
        root = Path(directory)
        settings = Settings(
            project_root=root,
            knowledge_root=root / "knowledge",
            workspace_root=root / "workspace",
            repository_root=root / "repository",
            runtime_root=root / "runtime",
        )
        settings.ensure_directories()
        settings.repository_root.mkdir()
        async with Client(create_server(settings)) as client:
            tools = (await client.list_tools()).tools
    lines = [
        "# MCP 工具契约",
        "",
        "> 本文由 `scripts/generate_tools_doc.py` 从真实 `tools/list` 结果生成，请勿手改。",
        "",
        "统一返回 `{ok, data, error, meta}`；`meta` 至少含 "
        "`trace_id/tool/duration_ms/attempts/caller`。",
        "",
    ]
    for tool in tools:
        lines.extend([f"## `{tool.name}`", "", tool.description or "", ""])
        annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        lines.extend(
            [
                f"- Annotations：`{json.dumps(annotations, ensure_ascii=False)}`",
                f"- 失败语义：{ERRORS[tool.name]}",
                f"- 自动重试：{RETRY[tool.name]}",
                "",
                "| 参数 | 类型 | 必填 | 默认值 | 说明 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        required = set(tool.input_schema.get("required", []))
        for name, schema in tool.input_schema.get("properties", {}).items():
            default = schema.get("default", "—")
            lines.append(
                f"| `{name}` | {schema_type(schema)} | "
                f"{'是' if name in required else '否'} | `{default}` | "
                f"{schema.get('description', '')} |"
            )
        lines.extend(["", "返回：统一 ToolResult；具体 `data` 字段见工具描述与 API 示例。", ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = asyncio.run(render())
    target = Path(__file__).resolve().parents[1] / "docs" / "TOOLS.md"
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != output:
            raise SystemExit("docs/TOOLS.md is stale; run scripts/generate_tools_doc.py")
        return
    target.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
