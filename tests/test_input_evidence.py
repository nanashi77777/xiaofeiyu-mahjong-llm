import threading
import time
from types import SimpleNamespace

import pytest

from majsoul_llm.execution import Executor, Target
from majsoul_llm.input_lease import InputLease
from majsoul_llm.native import InputDeferred
from majsoul_llm.rules import legal_actions


@pytest.mark.parametrize('outcome',['sent','uncertain','evidence_io_error'])
async def test_non_discard_evidence_uses_original_frame_and_never_represses(state,tmp_path,outcome):
    executor=object.__new__(Executor)
    executor.state_getter=lambda:state
    executor.input_mode='browser_background'
    executor.lease=InputLease()
    executor.mouse=SimpleNamespace(stop=threading.Event(),user=SimpleNamespace(GetAsyncKeyState=lambda key:0))
    original_frame=b'fixture previously captured frame'
    captured=[]
    async def target(*args):
        return Target((10,20),(0,0,100,100),time.monotonic(),'visible_fixture',
                      (10,20),{'same_layout':True},original_frame,.99)
    async def layout():
        return {'same_layout':True}
    async def press(point,deadline,stop,validate,on_press=None):
        await validate()
        if on_press:
            on_press()
        captured.append(point)
        if outcome=='uncertain':
            raise InputDeferred('possibly pressed')
        return time.monotonic()
    executor.target=target
    executor.browser=SimpleNamespace(settings=SimpleNamespace(runtime=tmp_path),
                                    layout_metrics=layout,background_click=press)
    action=legal_actions(state)[0].model_copy(update={'kind':'pass'})
    if outcome=='evidence_io_error':
        (tmp_path/'input-evidence').write_bytes(b'blocked folder')
    if outcome=='uncertain':
        with pytest.raises(InputDeferred,match='possibly pressed'):
            await executor.execute(state,action,time.monotonic()+3)
    else:
        await executor.execute(state,action,time.monotonic()+3)
    assert captured==[(10,20)] and executor.lease.owner is None
    step=next(m for m in executor.metrics if m['stage']=='target_evidence')
    assert step['window_id']==action.window_id and step['page_point']==(10,20)
    assert step['confidence']==.99
    if outcome=='evidence_io_error':
        assert 'screenshot' not in step
        assert any(m['stage']=='target_evidence_unavailable' for m in executor.metrics)
    else:
        assert (tmp_path/'input-evidence'/step['screenshot']).read_bytes()==original_frame
    assert executor.target_evidence==[]
