import asyncio
import threading
from types import SimpleNamespace

import pytest

from majsoul_llm.domain import Phase
from majsoul_llm.input_lease import InputLease
from majsoul_llm.lobby import LobbyError
from majsoul_llm.round_flow import RoundFlow


def result_service(state,store,tmp_path):
    stop = threading.Event()
    stop.set()
    service = SimpleNamespace(engine=SimpleNamespace(state=state),
        coordinator=SimpleNamespace(enabled=False,active=lambda:False),navigator=SimpleNamespace(busy=lambda:False),
        executor=SimpleNamespace(mouse=SimpleNamespace(stop=stop)),input_lease=InputLease(),store=store,
        settings=SimpleNamespace(runtime=tmp_path))
    async def image():
        return b'isolated-fixture-only'
    service.browser = SimpleNamespace(image_bytes=image)
    return service


async def test_explicit_result_resume_confirms_once_without_enabling_decisions(state,store,tmp_path):
    state.phase = Phase.ENDED
    state.lifecycle = 'ended'
    service = result_service(state,store,tmp_path)
    calls = []
    class UI:
        async def observe(self):
            return 'final' if not calls else 'room'
        async def click(self,scene,control,ctx,submit):
            ctx.check()
            ctx.before_submit(submit)
            calls.append((scene,control,submit))
    service.lobby_ui = UI()
    flow = RoundFlow(service)
    await flow.resume()
    with pytest.raises(LobbyError,match='input_busy'):
        await flow.resume()
    await flow.task
    assert calls == [('final','confirm','confirm_result')]
    assert service.coordinator.enabled is False
    assert service.input_lease.owner is None
    assert not store.records('decision')
    assert store.records('result_ui')[0]['payload']['status'] == 'submitted'


@pytest.mark.parametrize('interference',['stop','new_round'])
async def test_explicit_result_resume_cancels_before_press_on_interference(state,store,tmp_path,interference):
    state.phase = Phase.ENDED
    state.lifecycle = 'ended'
    service = result_service(state,store,tmp_path)
    started,release = asyncio.Event(),asyncio.Event()
    class UI:
        async def observe(self):
            started.set()
            await release.wait()
            return 'final'
        async def click(self,*args,**kwargs):
            raise AssertionError('superseded result must not click')
    service.lobby_ui = UI()
    flow = RoundFlow(service)
    await flow.resume()
    await started.wait()
    if interference == 'stop':
        service.executor.mouse.stop.set()
    else:
        service.engine.state = state.model_copy(update={'round_uid':'next-round','phase':Phase.WAITING})
    release.set()
    await flow.task
    assert not any(r['payload']['status']=='submitted' for r in store.records('result_ui'))
    assert service.coordinator.enabled is False


async def test_result_resume_rejects_active_hand_and_other_input_owners(state,store,tmp_path):
    service = result_service(state,store,tmp_path)
    flow = RoundFlow(service)
    with pytest.raises(LobbyError,match='authoritative_result'):
        await flow.resume()
    assert service.executor.mouse.stop.is_set()
    state.phase = Phase.ROUND_END
    for owner in ('navigation','gameplay','lease'):
        service.navigator.busy = lambda owner=owner:owner=='navigation'
        service.coordinator.enabled = owner=='gameplay'
        service.input_lease.owner = 'fixture' if owner=='lease' else None
        with pytest.raises(LobbyError,match='input_busy'):
            await flow.resume()
    assert flow.task is None and not store.records('result_ui')
