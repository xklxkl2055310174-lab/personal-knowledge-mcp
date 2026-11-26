from __future__ import annotations

import base64
import json
from pathlib import Path

from knowledge_mcp.provider_store import ProviderStore
from knowledge_mcp.providers import ProviderConfig


def test_provider_store_round_trip_never_writes_plaintext_key(tmp_path: Path) -> None:
    def encode(value: str) -> str:
        return base64.b64encode(value.encode()).decode()

    def decode(value: str) -> str:
        return base64.b64decode(value).decode()

    store = ProviderStore(tmp_path / "provider.json", encode, decode)
    config = ProviderConfig.from_payload(
        {
            "id": "deepseek",
            "model": "deepseek-chat",
            "api_key": "secret-test-key",
        }
    )
    assert config is not None

    public = store.save(config)
    raw = store.path.read_text(encoding="utf-8")
    restored = store.load()

    assert "secret-test-key" not in raw
    assert "api_key" not in public
    assert "encrypted_api_key" not in public
    assert public["saved"] is True
    assert restored is not None
    assert restored.api_key == "secret-test-key"
    assert json.loads(raw)["id"] == "deepseek"


def test_provider_store_delete(tmp_path: Path) -> None:
    store = ProviderStore(tmp_path / "provider.json", lambda value: value, lambda value: value)
    config = ProviderConfig.from_payload(
        {
            "id": "deepseek",
            "model": "deepseek-chat",
            "api_key": "key",
        }
    )
    assert config is not None
    store.save(config)

    store.delete()

    assert store.public() is None
