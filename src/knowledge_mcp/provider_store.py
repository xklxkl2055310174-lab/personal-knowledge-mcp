"""Persist one provider configuration without storing its API key as plaintext.

Windows DPAPI binds the encrypted value to the current Windows user.  The JSON file is also
kept under the git-ignored .private directory.  Other platforms must configure the provider
through environment variables until an OS-backed secret store is added for them.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .providers import ProviderConfig, ProviderError, provider_catalog


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    value = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return value, buffer


def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    if os.name != "nt":
        raise ProviderError("持久化 API Key 当前仅支持 Windows DPAPI")
    source, source_buffer = _blob(secret.encode("utf-8"))
    encrypted = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    protect = crypt32.CryptProtectData
    protect.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    protect.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = protect(
        ctypes.byref(source),
        "personal-knowledge-mcp",
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(encrypted),
    )
    _ = source_buffer
    if not ok:
        raise ProviderError(f"Windows DPAPI 加密失败：{ctypes.get_last_error()}")
    try:
        payload = ctypes.string_at(encrypted.pbData, encrypted.cbData)
        return base64.b64encode(payload).decode("ascii")
    finally:
        kernel32.LocalFree(encrypted.pbData)


def unprotect_secret(encrypted_secret: str) -> str:
    if not encrypted_secret:
        return ""
    if os.name != "nt":
        raise ProviderError("持久化 API Key 当前仅支持 Windows DPAPI")
    try:
        encrypted_bytes = base64.b64decode(encrypted_secret, validate=True)
    except ValueError as exc:
        raise ProviderError("已保存的 API Key 密文格式无效") from exc
    source, source_buffer = _blob(encrypted_bytes)
    decrypted = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    unprotect.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = unprotect(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(decrypted),
    )
    _ = source_buffer
    if not ok:
        raise ProviderError(
            "无法解密已保存的 API Key；它只能由保存时的 Windows 用户读取"
        )
    try:
        return ctypes.string_at(decrypted.pbData, decrypted.cbData).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProviderError("已保存的 API Key 解密结果无效") from exc
    finally:
        kernel32.LocalFree(decrypted.pbData)


@dataclass(slots=True)
class ProviderStore:
    path: Path
    protect: Callable[[str], str] | None = None
    unprotect: Callable[[str], str] | None = None

    def save(self, config: ProviderConfig) -> dict[str, Any]:
        protect = self.protect or protect_secret
        record = {
            "version": 1,
            "id": config.provider,
            "model": config.model,
            "base_url": config.base_url,
            "api_version": config.api_version,
            "protocol": config.protocol,
            "encrypted_api_key": protect(config.api_key),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return self.public()

    def load(self) -> ProviderConfig | None:
        record = self._read()
        if record is None:
            return None
        unprotect = self.unprotect or unprotect_secret
        return ProviderConfig.from_payload(
            {
                "id": record.get("id"),
                "model": record.get("model"),
                "base_url": record.get("base_url"),
                "api_key": unprotect(str(record.get("encrypted_api_key") or "")),
                "api_version": record.get("api_version"),
            }
        )

    def public(self) -> dict[str, Any] | None:
        record = self._read()
        if record is None:
            return None
        provider_id = str(record.get("id") or "")
        metadata = next(
            (item for item in provider_catalog() if item["id"] == provider_id),
            None,
        )
        protocol = str(record.get("protocol") or "")
        return {
            "id": provider_id,
            "label": (metadata or {}).get("label", provider_id),
            "model": str(record.get("model") or ""),
            "base_url": str(record.get("base_url") or ""),
            "api_version": str(record.get("api_version") or "2024-10-21"),
            "supports_tools": protocol in {"openai", "anthropic", "azure"},
            "saved": True,
        }

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

    def _read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderError("无法读取已保存的模型配置") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ProviderError("已保存的模型配置版本无效")
        return payload
