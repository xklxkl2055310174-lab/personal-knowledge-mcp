from __future__ import annotations

import io
import json
from typing import Any

import pytest

from knowledge_mcp.providers import (
    ProviderClient,
    ProviderConfig,
    ProviderError,
    provider_catalog,
)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def test_provider_catalog_contains_major_and_local_providers() -> None:
    ids = {item["id"] for item in provider_catalog()}

    assert {"local", "openai", "anthropic", "gemini", "deepseek", "qwen"} <= ids
    assert {"moonshot", "zhipu", "doubao", "azure-openai", "ollama", "custom"} <= ids


def test_provider_config_validates_and_never_exposes_key() -> None:
    config = ProviderConfig.from_payload(
        {"id": "openai", "model": "test-model", "api_key": "super-secret"}
    )

    assert config is not None
    assert config.base_url == "https://api.openai.com/v1"
    assert "api_key" not in config.public()
    assert "super-secret" not in str(config.public())


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"id": "missing"}, "不支持"),
        ({"id": "openai", "model": "m"}, "API Key"),
        (
            {"id": "custom", "model": "m", "base_url": "file:///tmp/model"},
            "http/https",
        ),
    ],
)
def test_provider_config_rejects_invalid_payload(payload: dict, message: str) -> None:
    with pytest.raises(ProviderError, match=message):
        ProviderConfig.from_payload(payload)


def test_openai_compatible_client_builds_request_and_parses_usage() -> None:
    seen = {}

    def urlopen(request: Any, timeout: float) -> FakeResponse:
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return FakeResponse(
            json.dumps(
                {
                    "choices": [{"message": {"content": "基于证据的回答"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 6},
                }
            ).encode()
        )

    config = ProviderConfig.from_payload(
        {
            "id": "deepseek",
            "model": "deepseek-chat",
            "api_key": "test-key",
        }
    )
    assert config is not None
    result = ProviderClient(config, urlopen=urlopen)._request(
        "MCP 是什么？", "mcp.md:1 | MCP 连接 Agent 与工具。"
    )

    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["body"]["model"] == "deepseek-chat"
    assert "mcp.md:1" in seen["body"]["messages"][1]["content"]
    assert result.text == "基于证据的回答"
    assert result.input_tokens == 12
    assert result.output_tokens == 6


def test_gemini_client_uses_native_header_and_response_shape() -> None:
    seen = {}

    def urlopen(request: Any, timeout: float) -> FakeResponse:
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        return FakeResponse(
            json.dumps(
                {
                    "candidates": [{"content": {"parts": [{"text": "Gemini 回答"}]}}],
                    "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 3},
                }
            ).encode()
        )

    config = ProviderConfig.from_payload(
        {"id": "gemini", "model": "gemini-test", "api_key": "gemini-key"}
    )
    assert config is not None
    result = ProviderClient(config, urlopen=urlopen)._request("问题", "证据")

    assert seen["url"].endswith("/models/gemini-test:generateContent")
    assert seen["headers"]["X-goog-api-key"] == "gemini-key"
    assert result.text == "Gemini 回答"
