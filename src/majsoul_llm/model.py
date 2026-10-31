"""Cancelable Responses client with strict terminal JSON and honest usage accounting."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid

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
        self.races = set()

    @property
    def credential_id(self):
        return hashlib.sha256(self.settings.api_key.get_secret_value().encode()).hexdigest()[:16]

    async def request(self, payload: dict, *, purpose="decision", deadline: float | None = None, system=None, output_schema=None,
                      on_text=None, trace=None):
        start = time.monotonic()
        credential = self.settings.api_key.get_secret_value()
        metrics = {"purpose": purpose, "model": self.settings.model, "usage": None, "ttft_ms": None,
                   "model_config": self.settings.model_status(),
                   "text_delta_count":0, "text_characters":0, "last_delta_ms":None,
                   "elapsed_ms": None, "status": "failed", "billing_scope":"mahjong",
                   "credential_id":hashlib.sha256(credential.encode()).hexdigest()[:16],
                   "request_q":payload.get('q'), "request_id":uuid.uuid4().hex,
                   **(trace or {})}
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
                            metrics['text_delta_count'] += 1
                            metrics['text_characters'] += len(event.get('delta',''))
                            metrics['elapsed_ms'] = (time.monotonic()-start)*1000
                            metrics['last_delta_ms'] = metrics['elapsed_ms']
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
        if self.settings.stream_hedge_delay_seconds:
            task = asyncio.current_task()
            self.races.add(task)
            try:
                return await self._race_decision(context,deadline)
            finally:
                self.races.discard(task)
        return await self._begin_decision(context,deadline)

    async def _race_decision(self, context, deadline):
        started = time.monotonic()
        race_id = uuid.uuid4().hex
        tasks, selected = {}, None
        def launch():
            lane = len(tasks)
            task = asyncio.create_task(self._begin_decision(context,deadline,attempts=1,
                trace={'race_id':race_id,'race_lane':lane}))
            tasks[task] = lane
        launch()
        failures = set()
        try:
            while True:
                candidates = []
                for task in tasks:
                    if task.done() and task not in failures:
                        try:
                            candidates.append((task.result(),task))
                        except (ModelFailure,ValueError,asyncio.CancelledError):
                            failures.add(task)
                if candidates:
                    selected, winner = min(candidates,key=lambda pair:pair[0].metrics['action_committed_mono'])
                    selected.metrics.update(action_ready_ms=(selected.metrics['action_committed_mono']-started)*1000,
                                            race_id=race_id,race_lane=tasks[winner],race_requests=len(tasks))
                    if self.store:
                        self.store.record('stream_race',{'race_id':race_id,'request_q':context.q,
                            'winner_lane':tasks[winner],'requests':len(tasks),'action_id':selected.action.id,
                            'action_ready_ms':selected.metrics['action_ready_ms']},
                            context.data.get('match_id'),context.data.get('round_uid'))
                    break
                remaining = deadline-time.monotonic()
                if remaining <= 0 or len(failures)==2:
                    raise ModelFailure('no valid model prefix before shared deadline')
                delay = started+self.settings.stream_hedge_delay_seconds-time.monotonic()
                if len(tasks)==1 and (delay <= 0 or failures):
                    launch()
                    continue
                pending = set(tasks)-failures
                await asyncio.wait(pending,timeout=min(remaining,max(0,delay)) if len(tasks)==1 else remaining,
                                   return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            try:
                results = await asyncio.gather(*tasks,return_exceptions=True)
                abandoned = [result.completion for result in results
                             if isinstance(result,StreamChoice) and result is not selected]
                for task in abandoned:
                    task.cancel()
                await asyncio.gather(*abandoned,return_exceptions=True)
            except BaseException:
                if selected:
                    selected.completion.cancel()
                    await asyncio.gather(selected.completion,return_exceptions=True)
                raise
        return selected

    async def _begin_decision(self, context: Context, deadline, *, attempts=2, trace=None):
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
                     'action_ready_ms':metrics['elapsed_ms'],'stream_action_format':format,
                     'action_committed_mono':time.monotonic()}
            ready.set_result((action,early))
            return True
        async def receive():
            try:
                for attempt in range(attempts):
                    try:
                        payload = context.data if attempt == 0 else {**context.data,
                            'repair_instruction':('上次动作段无效。严格先输出本请求合法a字段，再输出r/p。'
                                                  if format == 'short' else
                                                  '上次动作段无效。严格先输出本请求合法d对象，再输出r/p。')}
                        raw,metrics = await self.request(payload,deadline=deadline,on_text=on_text,
                            purpose='decision' if attempt == 0 else 'repair',
                            output_schema=context.stream_response_schema(format),
                            system=stream_prompt(prompt_for(context.data.get('v')),format),trace=trace)
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
                        if prefix.committed is not None or attempt+1 >= attempts or deadline-time.monotonic() < 1:
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
        races = list(self.races)
        for task in races:
            task.cancel()
        await asyncio.gather(*races,return_exceptions=True)
        for task in list(self.streams):
            task.cancel()
        await asyncio.gather(*self.streams,return_exceptions=True)
        await self.http.aclose()
