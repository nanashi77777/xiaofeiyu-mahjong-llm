"""Cancelable Responses client with strict terminal JSON and honest usage accounting."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

import httpx

from .config import Settings
from .domain import Decision
from .store import Store
from .wire import SYSTEM, Context


class ModelFailure(RuntimeError):
    pass


class ModelClient:
    def __init__(self, settings: Settings, store: Store | None = None, transport=None):
        self.settings, self.store = settings, store
        self.http = httpx.AsyncClient(base_url=settings.base_url.rstrip("/") + "/", timeout=settings.request_timeout,
                                      transport=transport, trust_env=False)

    @property
    def credential_id(self):
        return hashlib.sha256(self.settings.api_key.get_secret_value().encode()).hexdigest()[:16]

    async def request(self, payload: dict, *, purpose="decision", deadline: float | None = None, system=SYSTEM):
        start = time.monotonic()
        credential = self.settings.api_key.get_secret_value()
        metrics = {"purpose": purpose, "model": self.settings.model, "usage": None, "ttft_ms": None,
                   "elapsed_ms": None, "status": "failed", "billing_scope":"mahjong",
                   "credential_id":hashlib.sha256(credential.encode()).hexdigest()[:16]}
        body = {"model": self.settings.model, "reasoning": {"effort": "none"}, "store": False,
                "stream": True, "instructions": system,
                "input": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]}
        body["text"] = {"verbosity":"low"}
        if self.settings.json_schema and purpose == "decision":
            body["text"]["format"] = {"type": "json_schema", "name": "mahjong_decision", "strict": True,
                            "schema": Decision.model_json_schema()}
        text_parts, completed = [], False
        try:
            timeout = min(self.settings.request_timeout, deadline - time.monotonic()) if deadline else self.settings.request_timeout
            if timeout <= 0:
                raise ModelFailure("deadline exhausted")
            async with asyncio.timeout(timeout):
                async with self.http.stream("POST", "responses", headers={"Authorization": "Bearer " + credential}, json=body) as response:
                    if response.status_code != 200:
                        raise ModelFailure(f"model HTTP {response.status_code}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        content = line[5:].strip()
                        if not content or content == "[DONE]":
                            continue
                        event = json.loads(content)
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            if metrics["ttft_ms"] is None:
                                metrics["ttft_ms"] = (time.monotonic() - start) * 1000
                            text_parts.append(event.get("delta", ""))
                        if kind in ("response.completed", "response.incomplete"):
                            result = event.get("response", {})
                            metrics["usage"] = result.get("usage")
                            if kind != "response.completed" or result.get("status", "completed") != "completed":
                                raise ModelFailure("incomplete model response")
                            completed = True
                            if not text_parts:
                                text_parts = [c.get("text", "") for o in result.get("output", []) for c in o.get("content", []) if c.get("type") == "output_text"]
                        if kind in ("response.failed", "error"):
                            raise ModelFailure("model stream failed")
            if not completed:
                raise ModelFailure("model stream has no completed terminal event")
            output = json.loads("".join(text_parts))
            if not isinstance(output, dict):
                raise ModelFailure("model output is not an object")
            metrics["status"] = "completed"
            return output, metrics
        except (TimeoutError, httpx.HTTPError, json.JSONDecodeError) as error:
            metrics["error_type"] = type(error).__name__
            raise ModelFailure(type(error).__name__) from None
        except asyncio.CancelledError:
            metrics["status"] = "cancelled"
            raise
        finally:
            metrics["elapsed_ms"] = (time.monotonic() - start) * 1000
            if self.store:
                self.store.record("token_usage", metrics, payload.get("match_id"), payload.get("round_uid"))

    async def decide(self, context: Context, deadline):
        payload = context.data
        for attempt in range(2):
            raw = None
            try:
                raw, metrics = await self.request(payload, purpose="decision" if attempt == 0 else "repair", deadline=deadline)
                decision, action = context.validate(raw)
                return decision, action, metrics
            except (ModelFailure, ValueError) as error:
                if self.store and raw is not None:
                    self.store.record("invalid_decision", {"raw_output":raw,"error_type":type(error).__name__},
                                      payload.get("match_id"),payload.get("round_uid"))
                if attempt or deadline - time.monotonic() < 1.0:
                    raise
                payload = {**context.data, "repair_instruction": "上次未返回有效完整JSON或引用无效；请仅选择本请求有效action_id，按约定完整输出。"}
        raise ModelFailure("no valid decision")

    async def close(self):
        await self.http.aclose()
