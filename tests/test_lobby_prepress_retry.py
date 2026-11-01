"""Real lobby click pipeline retries only renderer failures before dispatch."""
import threading
import time
from types import SimpleNamespace

import pytest

from majsoul_llm.lobby import LobbyError
from majsoul_llm.lobby_ui import LobbyUI
from majsoul_llm.native import TargetExpired


def fixture_ui(fault):
    calls,submissions,presses,deadlines=[],[],[],[]
    stop=threading.Event()
    class Context:
        changed=False
        def check(self):
            if stop.is_set():
                raise LobbyError('navigation_stopped')
            if self.changed:
                raise LobbyError('fixture_state_changed')
        def before_submit(self,method):
            self.check()
            submissions.append(method)
    ctx=Context()
    ui=object.__new__(LobbyUI)
    layout={'canvas':{'x':0,'y':0,'width':100,'height':100}}
    async def metrics():return layout
    async def screenshot():return b'fixture'
    async def wait_scene(*args):ctx.check()
    async def click(point,deadline,flag,validate,on_press):
        calls.append(point)
        deadlines.append(deadline)
        await validate()
        if fault in ('always','once','stop','state') and (fault!='once' or len(calls)==1):
            if fault=='stop':
                stop.set()
            if fault=='state':
                ctx.changed=True
            raise TargetExpired('frame was not acknowledged before press')
        on_press()
        presses.append(point)
        if fault=='after':
            raise TargetExpired('ambiguous response after down')
        if fault=='unknown':
            raise RuntimeError('ambiguous transport')
        return time.monotonic()
    ui.browser=SimpleNamespace(image_bytes=screenshot,layout_metrics=metrics,
        background_click=click,require_visible_canvas=lambda _:None,page=object())
    ui.executor=SimpleNamespace(input_mode='browser_background',mouse=SimpleNamespace(
        stop=stop,user=SimpleNamespace(GetAsyncKeyState=lambda _:0)))
    ui.profile=lambda:{'width':100,'height':100,'scenes':{'fixture':{'controls':{'create':{'point':[50,60]}}}}}
    ui.wait_scene=wait_scene
    ui._image=lambda *_:None
    ui._scene=lambda *_:'fixture'
    return ui,ctx,calls,submissions,presses,deadlines


@pytest.mark.parametrize('fault,attempts,pressed',[
    ('once',2,1),('always',3,0),('after',1,1),('unknown',1,1),('stop',1,0),('state',1,0)])
async def test_lobby_prepress_boundaries(fault,attempts,pressed):
    ui,ctx,calls,submissions,presses,deadlines=fixture_ui(fault)
    started=time.monotonic()
    if fault=='once':
        await ui.click('fixture','create',ctx,submit='createRoom')
    else:
        with pytest.raises((TargetExpired,LobbyError,RuntimeError)):
            await ui.click('fixture','create',ctx,submit='createRoom')
    assert len(calls)==attempts and len(set(calls))==1
    assert len(presses)==pressed
    assert submissions==(['createRoom'] if pressed else [])
    assert all(deadline<=started+8.01 for deadline in deadlines)


async def test_all_reobservations_share_original_control_deadline():
    ui,ctx,*_=fixture_ui('always')
    observed=[]
    async def once(scene,control,context,deadline,before_press):
        observed.append((scene,control,deadline))
        raise TargetExpired('before press')
    ui._click_once=once
    with pytest.raises(TargetExpired):
        await ui.click('fixture','create',ctx,submit='createRoom')
    assert len(observed)==3 and len(set(observed))==1


async def test_physical_predown_submission_commits_on_service_thread():
    ui,ctx,calls,submissions,presses,_=fixture_ui('once')
    service_thread=threading.get_ident()
    async def geometry(script):
        return {'x':0,'y':0,'dpr':1}
    ui.browser.page=SimpleNamespace(evaluate=geometry)
    ui.executor.input_mode='physical_foreground'
    ui.executor.window=object()
    ui.executor.mouse.rect=lambda _:(0,0,100,100)
    def submit(method):
        assert threading.get_ident()==service_thread
        submissions.append(method)
    ctx.before_submit=submit
    def native_click(window,rect,point,observed,deadline,on_press):
        assert threading.get_ident()!=service_thread
        calls.append(point)
        if len(calls)==1:
            raise TargetExpired('fixture expired before physical down')
        on_press()
        presses.append(point)
        return time.monotonic()
    ui.executor.mouse.click=native_click
    await ui.click('fixture','create',ctx,submit='createRoom')
    assert len(calls)==2 and len(presses)==1 and submissions==['createRoom']
