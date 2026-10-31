import threading
import time
from types import SimpleNamespace

import pytest

from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.input_lease import InputLease
from majsoul_llm.lobby import LobbyError
from majsoul_llm.lobby_session import LobbySession, sanitize_room_notification
from majsoul_llm.practice import PracticeRooms, RoomAction


def service(store, *, humans=1, robots=3, mode=2):
    session=LobbySession()
    session.authenticated=True
    session.room={'room_code':'12345','humans':humans,'robots':robots,'players':4 if mode!=3 else 1,
                  'playing':False,'mode':{'mode':mode}}
    stop=threading.Event()
    calls=[]
    async def click(scene, control, ctx, *, submit=None):
        ctx.check()
        calls.append((scene,control))
        if submit:
            ctx.before_submit(submit)
            session.latest[submit]={'observed_mono':time.monotonic(),'error_code':0}
    return SimpleNamespace(store=store,engine=SimpleNamespace(lobby_session=session,state=None),
                           input_lease=InputLease(),navigator=SimpleNamespace(busy=lambda:False),
                           coordinator=SimpleNamespace(enabled=False),executor=SimpleNamespace(mouse=SimpleNamespace(stop=stop)),
                           lobby_ui=SimpleNamespace(click=click)), calls


async def test_practice_start_requires_solo_table_and_does_not_repeat(store):
    s,calls=service(store)
    command=RoomAction(command_id='solo-start',room_code='12345',action='start')
    assert (await PracticeRooms(s).run(command))['status']=='confirmed'
    assert (await PracticeRooms(s).run(command))['status']=='confirmed'
    assert calls==[('room','start')]
    with pytest.raises(LobbyError,match='command_id_content_conflict'):
        await PracticeRooms(s).run(command.model_copy(update={'action':'leave'}))
    s,calls=service(store,humans=2,robots=2)
    with pytest.raises(LobbyError,match='practice_requires'):
        await PracticeRooms(s).run(command.model_copy(update={'command_id':'refused-human-table'}))
    assert calls==[]
    s,calls=service(store,mode=3,robots=0)
    assert (await PracticeRooms(s).run(command.model_copy(update={'command_id':'solo-ai'})))['status']=='confirmed'


async def test_practice_room_change_and_unknown_submission_never_click_again(store):
    s,calls=service(store)
    command=RoomAction(command_id='unknown-start',room_code='12345',action='start')
    store.record('room_command',{'request':command.model_dump(),'status':'submitted','method':'startRoom'})
    assert (await PracticeRooms(s).run(command))['status']=='submitted'
    assert not calls
    with pytest.raises(LobbyError,match='practice_room_changed'):
        await PracticeRooms(s).run(command.model_copy(update={'command_id':'wrong-room','room_code':'99999'}))


async def test_missing_room_ack_persists_uncertainty_and_duplicate_never_represses(store,monkeypatch):
    s,calls=service(store)
    async def no_ack(scene,control,ctx,*,submit=None):
        ctx.check()
        calls.append((scene,control))
        ctx.before_submit(submit)
    s.lobby_ui.click=no_ack
    monkeypatch.setattr(PracticeRooms,'ACK_TIMEOUT',0)
    command=RoomAction(command_id='missing-ack',room_code='12345',action='start')
    practice=PracticeRooms(s)
    result=await practice.run(command)
    assert result['status']=='uncertain' and result['reason']=='server_confirmation_not_observed'
    assert store.records('room_command',limit=1)[0]['payload']==result
    assert s.input_lease.owner is None
    assert await PracticeRooms(s).run(command)==result
    assert calls==[('room','start')]


async def test_visible_room_back_can_confirm_direct_leave_without_second_press(store):
    s,calls=service(store)
    async def click(scene,control,ctx,*,submit=None):
        ctx.check()
        ctx.before_submit(submit)
        calls.append((scene,control))
        s.engine.lobby_session.latest['leaveRoom']={'observed_mono':time.monotonic(),'error_code':0}
        s.engine.lobby_session.room=None
    s.lobby_ui.click=click
    command=RoomAction(command_id='direct-leave',room_code='12345',action='leave')
    rooms=PracticeRooms(s)
    assert (await rooms.run(command))['status']=='confirmed'
    assert (await rooms.run(command))['status']=='confirmed'
    assert calls==[('room','back')]


@pytest.mark.parametrize('scene',['confirm_leave','room'])
async def test_leave_dialog_requires_actual_scene_and_missing_ack_stays_uncertain(store,monkeypatch,scene):
    s,calls=service(store)
    async def no_ack(scene,control,ctx,*,submit=None):
        ctx.check()
        ctx.before_submit(submit)
        calls.append((scene,control))
    async def observe():
        return scene
    s.lobby_ui.click=no_ack
    s.lobby_ui.observe=observe
    monkeypatch.setattr(PracticeRooms,'ACK_TIMEOUT',0)
    rooms=PracticeRooms(s)
    command=RoomAction(command_id='dialog-leave',room_code='12345',action='leave')
    result=await rooms.run(command)
    assert result['status']==('dialog_opened' if scene=='confirm_leave' else 'uncertain')
    assert await rooms.run(command)==result
    assert calls==[('room','back')]


def test_room_updates_are_bound_to_authenticated_lobby_connection():
    session=LobbySession()
    session.connection='ours'
    session.room={'room_code':'12345','playing':False,'mode':{}}
    notice={'method':'.lq.NotifyRoomPlayerUpdate','connection':'other','data':{'humans':1,'robots':3}}
    session.observe(notice)
    assert 'robots' not in session.room
    session.observe({**notice,'connection':'ours'})
    assert session.room['robots']==3
    session.observe({'method':'.lq.NotifyRoomGameStart','connection':'ours','data':{}})
    assert session.room['playing'] and session.has_game
    raw={'account_id':123,'robot_count':3,'player_list':[{'nickname':'private'}]}
    assert sanitize_room_notification('NotifyRoomPlayerUpdate',raw)=={'robots':3,'humans':1}
    current = {**raw, 'robot_count':0, 'robots':[{'account_id':1}, {'account_id':2}, {'account_id':3}]}
    assert sanitize_room_notification('NotifyRoomPlayerUpdate',current)=={'robots':3,'humans':1}


async def test_long_clock_integration_bypasses_expired_normal_clock_and_stop_clears_pending(store,state):
    state.operation.fixed_ms=300000
    state.operation.add_ms=0
    state.operation.received_mono=time.monotonic()-60
    class Model:
        async def decide(self,context,deadline):
            assert deadline > time.monotonic()+200
            decision,action=context.validate({'q':context.q,'a':'a0','r':'保留进张','p':None})
            return decision,action,{}
    class Executor:
        mouse=SimpleNamespace(stop=threading.Event())
        async def execute(self,state,action,deadline):
            return time.monotonic()
    c=Coordinator(Settings(integration_test=True),SimpleNamespace(store=store,state=state),Model(),Executor())
    c.shadow=False
    await c.decide(state)
    assert c.pending and store.records('receipt')[0]['payload']['status']=='mouse_sent'
    await c.stop('test_stop')
    assert c.pending is None and store.records('receipt')[0]['payload']['status']=='cancelled'
