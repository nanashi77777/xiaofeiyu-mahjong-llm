import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from majsoul_llm.execution import Executor
from majsoul_llm.input_lease import InputLease
from majsoul_llm.locator import Box


@pytest.mark.parametrize('blocked_stage', ['capture', 'recognition'])
@pytest.mark.parametrize('invalidate', [None, 'version', 'stop', 'layout', 'deadline', 'window', 'failure'])
async def test_cancel_drains_only_started_frame_and_publishes_only_current(state, blocked_stage, invalidate):
    e = object.__new__(Executor)
    e.input_mode, e.window = 'browser_background', object()
    e.mouse = SimpleNamespace(stop=threading.Event())
    e.state_getter = lambda: state
    e.lease, e.metrics = InputLease(), []
    captures, moves = [], []
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    layout = {'width':100, 'height':100, 'canvas':None}
    layouts = [layout]
    async def prepare():
        pass
    async def move(*point):
        moves.append(point)
    async def metrics():
        return layouts[0]
    async def image():
        captures.append(1)
        if blocked_stage == 'capture':
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.001)
        return b'existing-frame'
    def recognize(*args):
        if blocked_stage == 'recognition':
            loop.call_soon_threadsafe(entered.set)
            release.wait(2)
        if invalidate == 'failure':
            raise ValueError('classification failed')
        return {('tile', (3,)):Box(10,20,10,10,.99,'fixture')}
    e.browser = SimpleNamespace(prepare_background=prepare, image_bytes=image, layout_metrics=metrics,
                               page=SimpleNamespace(mouse=SimpleNamespace(move=move)))
    e._recognize_candidates = recognize
    deadline = time.monotonic()+(.08 if invalidate=='deadline' else 2)
    task = asyncio.create_task(e.observe(state, [], deadline))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    assert e.lease.owner is not None
    if invalidate == 'version':
        state.state_version += 1
        # The observer holds a detached snapshot in production.
        e.state_getter = lambda: state.model_copy(update={'state_version':state.state_version+1})
    elif invalidate == 'stop':
        e.mouse.stop.set()
    elif invalidate == 'layout':
        layouts[0] = {**layout, 'width':200}
    elif invalidate == 'window':
        changed = state.model_copy(deep=True)
        changed.operation.window_id += '-next'
        e.state_getter = lambda: changed
    elif invalidate == 'deadline':
        await asyncio.sleep(.06)
    release.set()
    result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
    assert isinstance(result[0], asyncio.CancelledError)
    assert e.lease.owner is None and len(captures) == 1 and moves == [(5,5)]
    if invalidate is None:
        target = await e.target('tile', state, [3], deadline)
        assert target.point == (15,25) and target.confidence == .99
        assert len(captures) == 1  # Execution can reuse the frame just drained.
    else:
        assert not e.observed_targets
