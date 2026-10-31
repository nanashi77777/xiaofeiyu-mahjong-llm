import json

import httpx
import pytest
from pydantic import SecretStr

from majsoul_llm.config import Settings
from majsoul_llm.model import ModelClient
from majsoul_llm.usage import usage_summary


@pytest.mark.parametrize("purpose", ["decision", "repair", "review", "probe"])
async def test_all_mahjong_requests_use_project_key_and_tag_usage(store, purpose):
    seen = []
    def handler(request):
        seen.append(request.headers["authorization"])
        assert json.loads(request.content)["reasoning"] == {"effort":"none"}
        terminal = {"type":"response.completed", "response":{"status":"completed",
            "usage":{"input_tokens":4,"output_tokens":3,"total_tokens":7},
            "output":[{"content":[{"type":"output_text","text":"{}"}]}]}}
        return httpx.Response(200, text="data: " + json.dumps(terminal) + "\n\n")
    settings = Settings(api_key=SecretStr("synthetic-mahjong-key"))
    client = ModelClient(settings, store, httpx.MockTransport(handler))
    try:
        _, metrics = await client.request({}, purpose=purpose)
        assert seen == ["Bearer synthetic-mahjong-key"]
        assert metrics["billing_scope"] == "mahjong"
        assert metrics["credential_id"] == client.credential_id
        encoded = json.dumps(store.records("token_usage"))
        assert "synthetic-mahjong-key" not in encoded
        assert store.records("token_usage")[0]["payload"]["purpose"] == purpose
    finally:
        await client.close()


def test_usage_keeps_old_keys_unknown_usage_and_purposes_separate(store):
    usage = {"input_tokens":10, "output_tokens":3, "total_tokens":13,
             "input_tokens_details":{"cached_tokens":4}}
    store.record("token_usage", {"purpose":"decision", "usage":usage})
    store.record("token_usage", {"purpose":"decision", "usage":usage,
                                "billing_scope":"mahjong", "credential_id":"old"})
    for purpose in ("decision", "repair", "review"):
        store.record("token_usage", {"purpose":purpose, "usage":usage,
                                    "billing_scope":"mahjong", "credential_id":"current"})
    store.record("token_usage", {"purpose":"review", "usage":None, "status":"cancelled",
                                "billing_scope":"mahjong", "credential_id":"current"})
    groups = usage_summary(store, "current")["groups"]
    current = next(g for g in groups if g["current"])
    assert current["requests"] == 4 and current["with_usage"] == 3 and current["without_usage"] == 1
    assert current["total_tokens"] == 39 and current["cached_tokens"] == 12
    assert current["reasoning_tokens"] is None
    assert set(current["purposes"]) == {"decision", "repair", "review"}
    assert current["purposes"]["review"]["requests"] == 2
    assert next(g for g in groups if g["scope"] == "legacy_unattributed")["total_tokens"] == 13
    assert next(g for g in groups if g["credential_id"] == "old")["total_tokens"] == 13


def test_usage_records_with_same_timestamp_keep_insertion_order(store, monkeypatch):
    monkeypatch.setattr("majsoul_llm.store.utcnow", lambda:"2026-10-31T00:00:00+00:00")
    store.record("token_usage", {"usage":{"total_tokens":7}})
    store.record("token_usage", {"usage":None})
    assert store.records("token_usage",limit=1)[0]["payload"]["usage"] is None
