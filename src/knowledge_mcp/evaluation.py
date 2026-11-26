"""Reproducible local/real-model evaluation without mutating the demo workspace."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import shutil
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client

from .agent import (
    DelegatedAgent,
    KnowledgeAgent,
    ProviderSummaryAgent,
    SummaryAgent,
    ToolCallingAgent,
)
from .config import Settings
from .context import ContextBudgetManager, MetricsRecorder, estimate_tokens
from .providers import ProviderClient, ProviderConfig
from .server import create_server


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def load_tasks(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def remove_tree_with_retry(path: Path) -> None:
    """Remove an evaluation sandbox after Windows releases child-process handles."""
    for attempt in range(12):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 11:
                raise
            gc.collect()
            time.sleep(min(1.0, 0.1 * (attempt + 1)))


async def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(__file__).resolve().parents[2]
    source_settings = Settings.from_env(project)
    tasks = load_tasks(project / "eval" / "tasks.jsonl")
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="knowledge-mcp-eval-", ignore_cleanup_errors=True
    ) as directory:
        temp = Path(directory)
        shutil.copytree(source_settings.workspace_root, temp / "workspace")
        settings = Settings(
            project_root=project,
            knowledge_root=source_settings.knowledge_root,
            workspace_root=temp / "workspace",
            repository_root=source_settings.repository_root,
            runtime_root=temp / "runtime",
            command_timeout_seconds=source_settings.command_timeout_seconds,
            allowed_commands=source_settings.allowed_commands,
        )
        provider_config = ProviderConfig.from_environment()
        if args.mode == "single" and provider_config is None:
            raise RuntimeError(
                "--mode single 需要通过环境变量配置支持工具调用的真实 Provider"
            )
        if provider_config and not provider_config.public().get("supports_tools"):
            raise RuntimeError(
                f"Provider {provider_config.id} 不支持当前 Agent 所需的工具调用协议"
            )
        summary_agent = (
            ProviderSummaryAgent(ProviderClient(provider_config))
            if provider_config
            else SummaryAgent()
        )
        server = create_server(settings)
        async with Client(server) as client:
            knowledge_agent = KnowledgeAgent(
                client,
                ContextBudgetManager(args.max_tokens),
                MetricsRecorder(temp / "runtime" / "eval-metrics.jsonl"),
                summary_agent,
            )
            for task in tasks:
                started = time.perf_counter()
                if task["kind"] == "qa":
                    if provider_config:
                        model_client = ProviderClient(provider_config)
                        runtime_agent = (
                            ToolCallingAgent(
                                client,
                                model_client,
                                ContextBudgetManager(args.max_tokens),
                                context_strategy=args.strategy,
                            )
                            if args.mode == "single"
                            else DelegatedAgent(
                                client,
                                model_client,
                                ContextBudgetManager(args.max_tokens),
                                context_strategy=args.strategy,
                            )
                        )
                        output = await runtime_agent.run(task["query"])
                        observed = output.text
                        evidence = list(output.detail.get("evidence", []))
                        citation_valid = bool(evidence) and bool(
                            output.detail.get("citations_valid", False)
                        )
                        input_tokens = output.input_tokens or 0
                        output_tokens = output.output_tokens or estimate_tokens(observed)
                        tool_calls = int(output.detail.get("tool_calls", 0))
                    else:
                        trace = await knowledge_agent.answer_with_trace(
                            task["query"], args.strategy, mode="delegated"
                        )
                        observed = trace.answer
                        citation_valid = trace.citations_valid
                        input_tokens = trace.context.estimated_tokens
                        output_tokens = estimate_tokens(trace.answer)
                        tool_calls = sum(
                            1 for step in trace.steps if step.detail.get("tool")
                        )
                    expected = task.get("expected_terms", [])
                    matched = sum(term.casefold() in observed.casefold() for term in expected)
                    hit_rate = matched / len(expected) if expected else 1.0
                    success = hit_rate >= task.get("threshold", 0.6) and citation_valid
                    row = {
                        "id": task["id"],
                        "success": success,
                        "keypoint_hit_rate": hit_rate,
                        "citation_valid": citation_valid,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "tool_calls": tool_calls,
                        "retries": 0,
                        "error_category": None,
                    }
                else:
                    result = await client.call_tool(task["tool"], task["arguments"])
                    payload = result.structured_content
                    expected_ok = task.get("expect_ok", True)
                    category = ((payload or {}).get("error") or {}).get("category")
                    success = bool(payload) and payload.get("ok") == expected_ok
                    if task.get("expect_category"):
                        success = success and category == task["expect_category"]
                    serialized = json.dumps(payload, ensure_ascii=False)
                    row = {
                        "id": task["id"],
                        "success": success,
                        "keypoint_hit_rate": 1.0 if success else 0.0,
                        "citation_valid": True,
                        "input_tokens": estimate_tokens(json.dumps(task["arguments"])),
                        "output_tokens": estimate_tokens(serialized),
                        "tool_calls": 1,
                        "retries": max(
                            0, len((payload or {}).get("meta", {}).get("attempts", [])) - 1
                        ),
                        "error_category": category,
                    }
                row["latency_ms"] = int((time.perf_counter() - started) * 1000)
                rows.append(row)
    remove_tree_with_retry(Path(directory))
    latencies = [row["latency_ms"] for row in rows]
    failures: dict[str, int] = {}
    for row in rows:
        if row["error_category"]:
            failures[row["error_category"]] = failures.get(row["error_category"], 0) + 1
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": args.mode,
        "strategy": args.strategy,
        "model": (
            provider_config.public()
            if provider_config
            else {"id": "local", "label": "本地确定性模式，不可写入简历模型指标"}
        ),
        "task_count": len(rows),
        "metrics": {
            "success_rate": sum(row["success"] for row in rows) / len(rows),
            "keypoint_hit_rate": statistics.fmean(row["keypoint_hit_rate"] for row in rows),
            "citation_accuracy": statistics.fmean(row["citation_valid"] for row in rows),
            "avg_input_tokens": statistics.fmean(row["input_tokens"] for row in rows),
            "avg_output_tokens": statistics.fmean(row["output_tokens"] for row in rows),
            "p50_latency_ms": percentile(latencies, 0.5),
            "p95_latency_ms": percentile(latencies, 0.95),
            "avg_tool_calls": statistics.fmean(row["tool_calls"] for row in rows),
            "retry_count": sum(row["retries"] for row in rows),
            "failure_distribution": failures,
        },
        "rows": rows,
    }


def write_eval_doc(project: Path, report_name: str, report: dict[str, Any]) -> None:
    latest: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
    for path in sorted((project / "reports").glob("*.json")):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            key = (
                candidate["mode"],
                candidate["strategy"],
                candidate["model"].get("id", "unknown"),
            )
            latest[key] = (path.name, candidate)
        except (OSError, KeyError, json.JSONDecodeError):
            continue
    key = (report["mode"], report["strategy"], report["model"].get("id", "unknown"))
    latest[key] = (report_name, report)
    rows: list[str] = []
    for name, item in sorted(latest.values(), key=lambda pair: pair[0]):
        metrics = item["metrics"]
        label = (
            item["model"].get("label", item["model"].get("id", "unknown"))
            if item["model"].get("id") != "local"
            else "本地模式（不可作简历模型数据）"
        )
        rows.append(
            f"| {item['mode']} | {item['strategy']} | {label} | {item['task_count']} | "
            f"{metrics['success_rate']:.1%} | {metrics['keypoint_hit_rate']:.1%} | "
            f"{metrics['citation_accuracy']:.1%} | {metrics['avg_input_tokens']:.1f} / "
            f"{metrics['avg_output_tokens']:.1f} | {metrics['p50_latency_ms']} / "
            f"{metrics['p95_latency_ms']} ms | {metrics['avg_tool_calls']:.1f} | "
            f"{metrics['retry_count']} | `reports/{name}` |"
        )
    table_header = (
        "| 模式 | 策略 | 执行环境 | 任务数 | 成功率 | 要点命中率 | 引用准确率 | "
        "平均输入 / 输出 token | P50 / P95 | 平均工具调用 | 重试 | 报告 |"
    )
    content = f"""# 评测结果

最近报告：`reports/{report_name}`

{table_header}
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
{chr(10).join(rows)}

成功要求：期望要点命中率达到任务阈值，且知识问答引用必须来自本次证据。
本地确定性模式只用于 CI 回归；只有配置真实模型后生成的报告才可作为简历数字。
"""
    (project / "docs" / "EVAL.md").write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("single", "delegated"), default="delegated")
    parser.add_argument("--strategy", choices=("full", "relevant", "summary"), default="relevant")
    parser.add_argument("--max-tokens", type=int, default=800)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args))
    project = Path(__file__).resolve().parents[2]
    report_root = project / "reports"
    report_root.mkdir(exist_ok=True)
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
    (report_root / name).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_eval_doc(project, name, report)
    print(json.dumps({"report": name, **report["metrics"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
