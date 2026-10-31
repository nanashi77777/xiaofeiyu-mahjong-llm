import asyncio
import threading
from types import SimpleNamespace

from majsoul_llm.domain import Phase
from majsoul_llm.execution import Executor
from majsoul_llm.input_lease import InputLease,InputBusy
from majsoul_llm.locator import ScreenLocator


async def test_waiting_hand_preparation_is_read_only_and_repeats_only_after_hand_change(state):
    state.operation=None
    state.phase=Phase.WAITING
    calls=[]
    async def noop(*args):
        pass
    async def capture():
        return b'fixture-visible-image'
    def hand(raw,s):
        calls.append(tuple(s.hand))
        return {}
    executor=object.__new__(Executor)
    executor.state_getter=lambda:state
    executor.mouse=SimpleNamespace(stop=threading.Event())
    executor.lease=InputLease()
    executor.input_mode='browser_background'
    executor.idle_observation=None
    executor.idle_hand_signature=None
    executor.locator=SimpleNamespace(hand_signature=ScreenLocator.hand_signature,hand=hand)
    executor.browser=SimpleNamespace(prepare_background=noop,image_bytes=capture,
                                     page=SimpleNamespace(mouse=SimpleNamespace(move=noop)))
    await executor.refresh_idle_hand(state,True)
    await executor.idle_observation
    assert len(calls)==1 and executor.lease.owner is None
    await executor.refresh_idle_hand(state,True)
    assert len(calls)==1
    state.hand=state.hand[:-1]
    await executor.refresh_idle_hand(state,True)
    await executor.idle_observation
    assert len(calls)==2
    await executor.refresh_idle_hand(state,False)
    assert executor.idle_observation is None


async def test_cancelled_idle_recognition_finishes_before_releasing_input_lease(state):
    state.operation=None
    state.phase=Phase.WAITING
    started,release=threading.Event(),threading.Event()
    async def noop(*args):
        pass
    async def capture():
        return b'fixture'
    def hand(*args):
        started.set()
        release.wait(2)
    executor=object.__new__(Executor)
    executor.state_getter=lambda:state
    executor.mouse=SimpleNamespace(stop=threading.Event())
    executor.lease=InputLease()
    executor.input_mode='browser_background'
    executor.idle_observation=None
    executor.idle_hand_signature=None
    executor.locator=SimpleNamespace(hand_signature=ScreenLocator.hand_signature,hand=hand)
    executor.browser=SimpleNamespace(prepare_background=noop,image_bytes=capture,
                                     page=SimpleNamespace(mouse=SimpleNamespace(move=noop)))
    await executor.refresh_idle_hand(state,True)
    assert await asyncio.to_thread(started.wait,1)
    cancel=asyncio.create_task(executor.cancel_idle_observation())
    await asyncio.sleep(.01)
    try:
        async with executor.lease.hold('gameplay'):
            raise AssertionError('overlapping input owner')
    except InputBusy:
        pass
    release.set()
    await cancel
    assert executor.lease.owner is None
