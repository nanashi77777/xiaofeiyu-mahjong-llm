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
from .streaming import ActionPrefix, StreamChoice, stream_prompt, strict_json
from .wire import Context, prompt_for


class ModelFailure(RuntimeError):
    pass


def strict_schema(schema):
    """Strict output schemas express optional values as nullable required keys."""
    if isinstance(schema,list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema,dict):
        return schema
    result = {key:strict_schema(value) for key,value in schema.items() if key not in ('default','title')}
    if result.get('type') == 'object' and 'properties' in result:
        result['required'] = list(result['properties'])
        result['additionalProperties'] = False
    return result


class ModelClient:
    def __init__(self, settings: Settings, store: Store | None = None, transport=None):
        self.settings, self.store = settings, store
        self.http = httpx.AsyncClient(base_url=settings.base_url.rstrip("/") + "/", timeout=settings.request_timeout,
                                      transport=transport, trust_env=False)
        self.streams = set()

    @property
    def credential_id(self):
        return hashlib.sha256(self.settings.api_key.get_secret_value().encode()).hexdigest()[:16]

    async def request(self, payload: dict, *, purpose="decision", deadline: float | None = None, system=None, output_schema=None,
                      on_text=None):
        start = time.monotonic()
        credential = self.settings.api_key.get_secret_value()
        metrics = {"purpose": purpose, "model": self.settings.model, "usage": None, "ttft_ms": None,
                   "elapsed_ms": None, "status": "failed", "billing_scope":"mahjong",
                   "credential_id":hashlib.sha256(credential.encode()).hexdigest()[:16],
                   "request_q":payload.get('q')}
        body = {"model": self.settings.model, "reasoning": {"effort": "none"}, "store": False,
                "stream": True, "instructions": system if system is not None else prompt_for(payload.get('v')),
                "input": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]}
        body["text"] = {"verbosity":"low"}
        if self.settings.json_schema and (purpose in ("decision", "repair") or output_schema is not None):
            body["text"]["format"] = {"type": "json_schema", "name": "mahjong_"+purpose, "strict": True,
                            "schema": strict_schema(output_schema or Decision.model_json_schema())}
        text_parts, completed = [], False
        try:
            request_limit = max(self.settings.request_timeout, 90) if purpose == "review" else self.settings.request_timeout
            timeout = min(request_limit, deadline - time.monotonic()) if deadline else request_limit
            if timeout <= 0:
                raise ModelFailure("deadline exhausted")
            async with asyncio.timeout(timeout) as timeout_scope:
                # The outer scope enforces the absolute game deadline. Keep
                # socket reads valid for the bounded tail after an early commit.
                async with self.http.stream("POST", "responses", headers={"Authorization": "Bearer " + credential},
                                            json=body, timeout=request_limit) as response:
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
                            metrics['elapsed_ms'] = (time.monotonic()-start)*1000
                            if on_text and on_text(''.join(text_parts),metrics):
                                # The game input uses its original deadline.
                                # Only annotation reception gets the bounded
                                # request timeout after the action is committed.
                                timeout_scope.reschedule(start+request_limit)
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
            output = strict_json("".join(text_parts))
            if not isinstance(output, dict):
                raise ModelFailure("model output is not an object")
            metrics["status"] = "completed"
            return output, metrics
        except (TimeoutError, httpx.HTTPError, json.JSONDecodeError) as error:
            metrics["error_type"] = type(error).__name__
            raise ModelFailure(type(error).__name__) from None
        except (ModelFailure,ValueError) as error:
            metrics['error_type'] = type(error).__name__
            raise
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
                raw, metrics = await self.request(payload, purpose="decision" if attempt == 0 else "repair", deadline=deadline,
                                                  output_schema=context.response_schema())
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

    async def begin_decision(self, context: Context, deadline):
        ready = asyncio.get_running_loop().create_future()
        format = self.settings.stream_action_format
        prefix = ActionPrefix(context,format)
        def on_text(text,metrics):
            try:
                action = prefix.feed(text)
            except ValueError:
                raise ModelFailure('invalid stream action prefix') from None
            if action is None:
                return False
            early = {**metrics,'status':'action_ready',
                     'action_ready_ms':metrics['elapsed_ms'],'stream_action_format':format}
            ready.set_result((action,early))
            return True
        async def receive():
            try:
                for attempt in range(2):
                    try:
                        payload = context.data if attempt == 0 else {**context.data,
                            'repair_instruction':('上次动作段无效。严格先输出本请求合法a字段，再输出r/p。'
                                                  if format == 'short' else
                                                  '上次动作段无效。严格先输出本请求合法d对象，再输出r/p。')}
                        raw,metrics = await self.request(payload,deadline=deadline,on_text=on_text,
                            purpose='decision' if attempt == 0 else 'repair',
                            output_schema=context.stream_response_schema(format),
                            system=stream_prompt(prompt_for(context.data.get('v')),format))
                        if prefix.committed is None:
                            raise ModelFailure('no committed stream action')
                        expected = {'a','r','p'} if format == 'short' else {'d','r','p'}
                        actual = {'q':context.q,'a':raw.get('a')} if format == 'short' else raw.get('d')
                        if set(raw) != expected or actual != prefix.committed:
                            raise ModelFailure('stream tail changed the committed action')
                        decision,action = context.validate({**actual,'r':raw['r'],'p':raw['p']})
                        return decision,action,metrics
                    except (ModelFailure,ValueError):
                        # The prefix is irrevocable. Repairs are only allowed
                        # before any action was committed, never for its tail.
                        if prefix.committed is not None or attempt or deadline-time.monotonic() < 1:
                            raise
                raise ModelFailure('no committed stream action')
            except BaseException as error:
                if not ready.done():
                    if isinstance(error,asyncio.CancelledError):
                        ready.cancel()
                    else:
                        ready.set_exception(error)
                raise
        task = asyncio.create_task(receive())
        self.streams.add(task)
        task.add_done_callback(self.streams.discard)
        try:
            action,metrics = await ready
            return StreamChoice(action,metrics,task)
        except BaseException:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            raise

    async def close(self):
        for task in list(self.streams):
            task.cancel()
        await asyncio.gather(*self.streams,return_exceptions=True)
        await self.http.aclose()
