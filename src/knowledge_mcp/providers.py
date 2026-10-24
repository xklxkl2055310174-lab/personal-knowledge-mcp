"""LLM provider adapters used by the Web Agent.

API keys are accepted per request and are never persisted or returned to the browser.
OpenAI-compatible vendors share one small HTTP adapter; Anthropic, Gemini and Azure
use their native request shapes.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "id": "local",
        "label": "本地演示（无需 API）",
        "protocol": "local",
        "base_url": "",
        "default_model": "",
        "requires_key": False,
        "hint": "使用项目内置的确定性总结器，不产生 API 费用。",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-5-mini",
        "requires_key": True,
        "hint": "OpenAI Chat Completions API",
    },
    {
        "id": "anthropic",
        "label": "Anthropic Claude",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-5",
        "requires_key": True,
        "hint": "Anthropic Messages API",
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "protocol": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
        "requires_key": True,
        "hint": "Google generateContent API",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
        "requires_key": True,
        "hint": "OpenAI 兼容接口",
    },
    {
        "id": "qwen",
        "label": "阿里云通义千问",
        "protocol": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "requires_key": True,
        "hint": "DashScope OpenAI 兼容接口",
    },
    {
        "id": "moonshot",
        "label": "月之暗面 Kimi",
        "protocol": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "requires_key": True,
        "hint": "Moonshot OpenAI 兼容接口",
    },
    {
        "id": "zhipu",
        "label": "智谱 GLM",
        "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "requires_key": True,
        "hint": "智谱 OpenAI 兼容接口",
    },
    {
        "id": "doubao",
        "label": "火山方舟 / 豆包",
        "protocol": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "",
        "requires_key": True,
        "hint": "模型处填写方舟控制台中的 Endpoint ID 或模型 ID。",
    },
    {
        "id": "baidu",
        "label": "百度智能云千帆",
        "protocol": "openai",
        "base_url": "https://qianfan.baidubce.com/v2",
        "default_model": "ernie-4.0-8k",
        "requires_key": True,
        "hint": "千帆 OpenAI 兼容接口",
    },
    {
        "id": "hunyuan",
        "label": "腾讯混元",
        "protocol": "openai",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "default_model": "hunyuan-turbos-latest",
        "requires_key": True,
        "hint": "混元 OpenAI 兼容接口",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "protocol": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4.1-mini",
        "requires_key": True,
        "hint": "可通过统一接口选择多个厂商模型。",
    },
    {
        "id": "siliconflow",
        "label": "硅基流动 SiliconFlow",
        "protocol": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "requires_key": True,
        "hint": "OpenAI 兼容接口",
    },
    {
        "id": "azure-openai",
        "label": "Azure OpenAI",
        "protocol": "azure",
        "base_url": "",
        "default_model": "",
        "requires_key": True,
        "hint": "Base URL 填资源地址，模型填写 Deployment Name。",
    },
    {
        "id": "ollama",
        "label": "Ollama（本地模型）",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "qwen2.5:7b",
        "requires_key": False,
        "hint": "需要先在本机启动 Ollama 并拉取对应模型。",
    },
    {
        "id": "custom",
        "label": "自定义 OpenAI 兼容接口",
        "protocol": "openai",
        "base_url": "",
        "default_model": "",
        "requires_key": False,
        "hint": "适用于其他实现 /chat/completions 的服务。",
    },
)

_BY_ID = {item["id"]: item for item in PROVIDERS}


class ProviderError(RuntimeError):
    """Safe provider error that never contains an API key."""


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: str
    protocol: str
    model: str
    base_url: str
    api_key: str
    api_version: str = "2024-10-21"
    timeout_seconds: float = 60.0

    @classmethod
    def from_payload(cls, payload: Any) -> ProviderConfig | None:
        if payload in (None, {}, ""):
            return None
        if not isinstance(payload, dict):
            raise ProviderError("provider 必须是对象")
        provider = str(payload.get("id") or "").strip()
        if provider == "local":
            return None
        meta = _BY_ID.get(provider)
        if not meta:
            raise ProviderError("不支持的模型供应商")

        model = str(payload.get("model") or meta["default_model"]).strip()
        base_url = str(payload.get("base_url") or meta["base_url"]).strip().rstrip("/")
        api_key = str(payload.get("api_key") or "").strip()
        api_version = str(payload.get("api_version") or "2024-10-21").strip()
        if not model:
            raise ProviderError("模型名称不能为空")
        if not base_url:
            raise ProviderError("Base URL 不能为空")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderError("Base URL 必须是有效的 http/https 地址")
        if parsed.username or parsed.password:
            raise ProviderError("Base URL 中不能包含用户名或密码")
        if meta["requires_key"] and not api_key:
            raise ProviderError("该供应商需要 API Key")
        if len(api_key) > 8_000:
            raise ProviderError("API Key 长度异常")
        return cls(
            provider=provider,
            protocol=str(meta["protocol"]),
            model=model,
            base_url=base_url,
            api_key=api_key,
            api_version=api_version,
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "protocol": self.protocol,
            "supports_tools": self.protocol in {"openai", "anthropic", "azure"},
        }

    @classmethod
    def from_environment(cls) -> ProviderConfig | None:
        provider = os.getenv("LLM_PROVIDER", "").strip()
        if not provider:
            return None
        return cls.from_payload(
            {
                "id": provider,
                "model": os.getenv("LLM_MODEL", ""),
                "base_url": os.getenv("LLM_BASE_URL", ""),
                "api_key": os.getenv("LLM_API_KEY", ""),
            }
        )


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelTurn:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None


SYSTEM_PROMPT = """你是个人知识库 Agent 的总结子 Agent。
只能根据提供的知识库证据回答，不要编造证据中不存在的信息。
使用中文，先直接回答，再列出关键依据；引用时保留“文件:行号”。
如果证据不足，请明确说“根据当前知识库无法确定”。"""


class ProviderClient:
    def __init__(
        self,
        config: ProviderConfig,
        *,
        urlopen: Any = urllib.request.urlopen,
    ) -> None:
        self.config = config
        self.urlopen = urlopen

    async def answer(self, query: str, context: str) -> Completion:
        if not context.strip():
            return Completion(f"没有在知识库中找到与“{query}”相关的内容。")
        return await asyncio.to_thread(self._request, query, context)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelTurn:
        if self.config.protocol not in {"openai", "anthropic", "azure"}:
            raise ProviderError("当前供应商协议不支持工具调用")
        return await asyncio.to_thread(self._chat_request, messages, tools)

    def _chat_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelTurn:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.protocol == "anthropic":
            headers.update(
                {
                    "x-api-key": self.config.api_key,
                    "anthropic-version": "2023-06-01",
                }
            )
            body = {
                "model": self.config.model,
                "max_tokens": 1200,
                "system": SYSTEM_PROMPT,
                "messages": self._anthropic_messages(messages),
                "tools": [
                    {
                        "name": item["function"]["name"],
                        "description": item["function"].get("description", ""),
                        "input_schema": item["function"]["parameters"],
                    }
                    for item in tools
                ],
            }
            url = f"{self.config.base_url}/v1/messages"
        else:
            if self.config.protocol == "azure":
                deployment = quote(self.config.model, safe="-._")
                version = quote(self.config.api_version, safe="-._")
                url = (
                    f"{self.config.base_url}/openai/deployments/{deployment}/chat/completions"
                    f"?api-version={version}"
                )
                headers["api-key"] = self.config.api_key
                body = {"messages": messages, "tools": tools}
            else:
                url = f"{self.config.base_url}/chat/completions"
                if self.config.api_key:
                    headers["Authorization"] = f"Bearer {self.config.api_key}"
                body = {
                    "model": self.config.model,
                    "messages": messages,
                    "tools": tools,
                    "stream": False,
                }
        request = self._json_request(url, body, headers)
        try:
            with self.urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ProviderError(
                f"模型服务返回 HTTP {exc.code}：{self._http_error_message(exc)}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"无法连接模型服务：{type(exc).__name__}") from None
        try:
            return self._parse_turn(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ProviderError("模型工具调用响应无法解析") from None

    @staticmethod
    def _anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "assistant":
                content: list[dict[str, Any]] = []
                if message.get("content"):
                    content.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls") or []:
                    raw = call["function"].get("arguments") or "{}"
                    arguments = json.loads(raw) if isinstance(raw, str) else raw
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["function"]["name"],
                            "input": arguments,
                        }
                    )
                converted.append({"role": "assistant", "content": content})
            elif role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message["tool_call_id"],
                                "content": message.get("content", ""),
                            }
                        ],
                    }
                )
            else:
                converted.append({"role": "user", "content": str(message.get("content") or "")})
        return converted

    def _parse_turn(self, payload: dict[str, Any]) -> ModelTurn:
        usage = payload.get("usage") or {}
        if self.config.protocol == "anthropic":
            blocks = payload.get("content") or []
            text = "".join(
                str(block.get("text") or "") for block in blocks if block.get("type") == "text"
            )
            calls = tuple(
                ToolCall(str(block["id"]), str(block["name"]), dict(block.get("input") or {}))
                for block in blocks
                if block.get("type") == "tool_use"
            )
            return ModelTurn(
                text,
                calls,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
            )
        message = payload["choices"][0]["message"]
        calls = []
        for item in message.get("tool_calls") or []:
            raw = item["function"].get("arguments") or "{}"
            arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
            calls.append(ToolCall(str(item["id"]), str(item["function"]["name"]), arguments))
        return ModelTurn(
            str(message.get("content") or ""),
            tuple(calls),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )

    def _request(self, query: str, context: str) -> Completion:
        if self.config.protocol == "anthropic":
            request = self._anthropic_request(query, context)
        elif self.config.protocol == "gemini":
            request = self._gemini_request(query, context)
        elif self.config.protocol == "azure":
            request = self._azure_request(query, context)
        else:
            request = self._openai_request(query, context)

        started = time.perf_counter()
        try:
            with self.urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            message = self._http_error_message(exc)
            raise ProviderError(f"模型服务返回 HTTP {exc.code}：{message}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"无法连接模型服务：{type(exc).__name__}") from None
        except (json.JSONDecodeError, UnicodeError, ValueError, TypeError, KeyError, IndexError):
            raise ProviderError("模型服务返回了无法解析的响应") from None

        try:
            completion = self._parse(payload)
        except (TypeError, KeyError, IndexError, ValueError):
            raise ProviderError("模型响应中没有可用的文本内容") from None
        if not completion.text.strip():
            raise ProviderError("模型返回了空内容")
        _ = started  # Kept here so the request boundary is easy to instrument later.
        return completion

    def _user_prompt(self, query: str, context: str) -> str:
        return f"问题：\n{query}\n\n知识库证据：\n{context}"

    def _openai_request(self, query: str, context: str) -> urllib.request.Request:
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_prompt(query, context)},
            ],
            "stream": False,
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return self._json_request(f"{self.config.base_url}/chat/completions", body, headers)

    def _anthropic_request(self, query: str, context: str) -> urllib.request.Request:
        body = {
            "model": self.config.model,
            "max_tokens": 1_000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": self._user_prompt(query, context)}],
        }
        return self._json_request(
            f"{self.config.base_url}/v1/messages",
            body,
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            },
        )

    def _gemini_request(self, query: str, context: str) -> urllib.request.Request:
        model = quote(self.config.model, safe="-._")
        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": self._user_prompt(query, context)}],
                }
            ],
            "generationConfig": {"maxOutputTokens": 1_000},
        }
        return self._json_request(
            f"{self.config.base_url}/models/{model}:generateContent",
            body,
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-goog-api-key": self.config.api_key,
            },
        )

    def _azure_request(self, query: str, context: str) -> urllib.request.Request:
        deployment = quote(self.config.model, safe="-._")
        version = quote(self.config.api_version, safe="-._")
        url = (
            f"{self.config.base_url}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={version}"
        )
        body = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_prompt(query, context)},
            ]
        }
        return self._json_request(
            url,
            body,
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "api-key": self.config.api_key,
            },
        )

    @staticmethod
    def _json_request(
        url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def _parse(self, payload: dict[str, Any]) -> Completion:
        if self.config.protocol == "anthropic":
            text = "".join(
                str(block.get("text") or "")
                for block in payload["content"]
                if block.get("type") == "text"
            )
            usage = payload.get("usage") or {}
            return Completion(text, usage.get("input_tokens"), usage.get("output_tokens"))
        if self.config.protocol == "gemini":
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(str(part.get("text") or "") for part in parts)
            usage = payload.get("usageMetadata") or {}
            return Completion(
                text,
                usage.get("promptTokenCount"),
                usage.get("candidatesTokenCount"),
            )

        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "") for item in content if isinstance(item, dict)
            )
        usage = payload.get("usage") or {}
        return Completion(
            str(content),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )

    @staticmethod
    def _http_error_message(exc: urllib.error.HTTPError) -> str:
        try:
            raw = exc.read(8_000).decode("utf-8", errors="replace")
            payload = json.loads(raw)
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("code") or "请求失败")[:500]
            return str(payload.get("message") or "请求失败")[:500]
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            return "请求失败"


def provider_catalog() -> list[dict[str, Any]]:
    """Return a JSON-safe copy of provider metadata without secrets."""

    return [
        {
            **item,
            "supports_tools": item["protocol"] in {"openai", "anthropic", "azure"},
        }
        for item in PROVIDERS
    ]
