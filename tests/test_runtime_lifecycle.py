import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.domain import Phase
from majsoul_llm.engine import Engine
from majsoul_llm.execution import Executor
from majsoul_llm.input_lease import InputLease
from majsoul_llm.native import Window
from majsoul_llm.round_flow import RoundFlow
from majsoul_llm.queries import Queries


async def test_disconnect_invalidates_operation_before_async_notification(store, state):
    changes = []
    async def changed(s, e):
        changes.append(s)
    engine = Engine(store, on_state=changed)
    engine.state, engine.connection = state, 'ours'
    engine.socket_closed('unrelated')
    assert engine.state.operation
    engine.socket_closed('ours')
    assert engine.state.operation is None
    assert engine.state.lifecycle == 'interrupted' and engine.connection is None
    await asyncio.sleep(0)
    assert len(changes) == 1
    assert store.latest(state.match_id).operation is None


def test_live_freshness_follows_capture_heartbeat_not_turn_age(store, state):
    state.observed_at = '2020-01-01T00:00:00+00:00'
    store.save_snapshot(state)
    health = {'connected':True, 'age_seconds':1, 'match_id':state.match_id}
    q = Queries(store, 'test', lambda:health)
    assert q.live()['freshness']['status'] == 'live'
    health['connected'] = False
    assert q.live()['freshness']['status'] == 'last_observed'
    assert Queries(store, 'offline').live()['freshness']['status'] == 'last_observed'


async def test_cancellation_during_release_preserves_mouse_sent_before_confirmation(store, state):
    state.operation.fixed_ms, state.operation.add_ms = 300000, 0
    entered, release = asyncio.Event(), asyncio.Event()
    class Model:
        async def decide(self, context, deadline):
            decision, action = context.validate({'q':context.q, 'a':'a0', 'r':'保留进张', 'p':None})
            return decision, action, {}
    class FakeExecutor:
        mouse = SimpleNamespace(stop=threading.Event())
        async def execute(self, s, a, deadline):
            entered.set()
            await release.wait()
            return time.monotonic()
    c = Coordinator(Settings(integration_test=True), SimpleNamespace(store=store, state=state), Model(), FakeExecutor())
    c.shadow = False
    c.task = asyncio.create_task(c.decide(state))
    await entered.wait()
    c.task.cancel()
    await asyncio.sleep(0)
    assert not c.task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await c.task
    assert store.records('receipt')[0]['payload']['status'] == 'mouse_sent'
    assert c.pending


async def test_binding_rejects_another_game_process():
    executor = object.__new__(Executor)
    async def process_id():
        return 123
    executor.browser = SimpleNamespace(process_id=process_id)
    executor.mouse = SimpleNamespace(windows=lambda:[Window(10, 999, 'Steam 雀魂'), Window(20, 123, '雀魂 Chrome')],
                                     stop=threading.Event())
    with pytest.raises(ValueError):
        await executor.bind(10)
    await executor.bind_dedicated()
    assert executor.window.hwnd == 20


async def test_result_flow_only_confirms_observed_results_and_cancels_on_new_round(store, state, tmp_path):
    state.phase = Phase.ROUND_END
    clicked = []
    stop = threading.Event()
    async def observe():
        return 'round_win'
    async def click(scene, control, ctx):
        ctx.check()
        clicked.append((scene, control))
        raise TimeoutError('release scene transition')
    async def image_bytes():
        return b'isolated-result-image'
    service = SimpleNamespace(engine=SimpleNamespace(state=state), store=store,
                              settings=SimpleNamespace(runtime=tmp_path), browser=SimpleNamespace(image_bytes=image_bytes),
                              executor=SimpleNamespace(mouse=SimpleNamespace(stop=stop)),
                              input_lease=InputLease(), lobby_ui=SimpleNamespace(observe=observe, click=click))
    flow = RoundFlow(service)
    await flow.changed(state, False)
    assert flow.task is None
    await flow.changed(state, True)
    await asyncio.sleep(.45)
    assert clicked == [('round_win','confirm')]
    service.engine.state = state.model_copy(update={'round_uid':'new-round', 'phase':Phase.DISCARD})
    await flow.changed(service.engine.state, True)
    await flow.stop()
    assert clicked == [('round_win','confirm')]
    assert service.input_lease.owner is None
