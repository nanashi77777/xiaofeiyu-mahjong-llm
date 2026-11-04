import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from majsoul_llm.app import create_app
from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.domain import Event
from majsoul_llm.engine import Engine
from majsoul_llm.input_lease import InputLease
from majsoul_llm.lobby import LobbyError
from majsoul_llm.lobby_session import LobbySession
from majsoul_llm.memory import Memories
from majsoul_llm.model import ModelClient
from majsoul_llm.model_profiles import ModelProfiles
from majsoul_llm.operations import Operations, OperationRequest, profile_id
from majsoul_llm.timing import TimeBank


@pytest.fixture
def service(store,tmp_path):
    settings=Settings(runtime=tmp_path,api_key=SecretStr('fixture-private-key'))
    session=LobbySession()
    session.authenticated=True
    started=[]
    stopped=[]
    async def noop(*args,**kwargs):
        pass
    coordinator=SimpleNamespace(enabled=False,shadow=False,status='stop',pending=None,
        annotation_tasks=set(),generation=0,active=lambda:False,bank=TimeBank(),
        recovery=SimpleNamespace(public=lambda:{'phase':'stopped'}))
    async def start(*,shadow):
        coordinator.enabled=True
        coordinator.shadow=shadow
        started.append(shadow)
    async def stop(reason):
        coordinator.enabled=False
        coordinator.generation+=1
        coordinator.status=reason
        stopped.append(reason)
    coordinator.start,coordinator.stop=start,stop
    s=SimpleNamespace(settings=settings,store=store,engine=SimpleNamespace(state=None,lobby_session=session,authenticated_mono=1),
        executor=SimpleNamespace(mouse=SimpleNamespace(stop=threading.Event()),window=object(),input_mode='browser_background',bind_dedicated=noop),
        browser=SimpleNamespace(connected=True,start=noop,fit_window=noop,release_background=noop,image_bytes=noop),
        navigator=SimpleNamespace(busy=lambda:False,stop=noop),lobby_ui=SimpleNamespace(profile=lambda:{"scenes":{"room":{"controls":{}}}}),coordinator=coordinator,input_lease=InputLease(),
        round_flow=SimpleNamespace(task=None,last_scene='room',stop=noop,resume=noop),client_guard=SimpleNamespace(stop=noop),
        reviewer=SimpleNamespace(queued=set(),review_task=None,pause_for_decision=noop,resume_pending=noop))
    s.client=ModelClient(settings,store)
    coordinator.model=s.reviewer.client=s.client
    s.operations=Operations(s)
    s.started,s.stopped=started,stopped
    yield s


def request(cid,action,**kwargs):
    return OperationRequest(command_id=cid,command={'action':action,**kwargs})


async def finish(s,r):
    await s.operations.submit(r)
    await s.operations.task
    return s.operations.get(r.command_id)


async def test_mutation_is_durable_idempotent_and_stop_never_restarts(service,state):
    s=service
    s.engine.state=state
    r=request('current-start','start')
    result=await finish(s,r)
    assert result['status']=='succeeded' and s.started==[False]
    await s.operations.submit(request('stop-now','stop'))
    assert not s.coordinator.enabled
    assert (await s.operations.submit(r))==result
    assert s.started==[False]
    with pytest.raises(LobbyError,match='content_conflict'):
        await s.operations.submit(request('current-start','shadow'))
    await s.client.close()


async def test_stopping_during_bind_cannot_enable_later(service,state):
    s=service
    s.engine.state=state
    entered=asyncio.Event()
    async def bind(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    s.executor.bind_dedicated=bind
    r=request('slow-start','start')
    await s.operations.submit(r)
    await entered.wait()
    await s.operations.submit(request('interrupt','emergency'))
    assert s.operations.get(r.command_id)['status']=='cancelled'
    assert not s.started and not s.coordinator.enabled and s.executor.mouse.stop.is_set()
    await s.client.close()


async def test_arm_only_hands_off_fresh_new_match_and_stop_revokes(service,state):
    s=service
    arm=request('armed','arm',seconds=10)
    assert (await finish(s,arm))['status']=='waiting_game'
    s.engine.state=state
    s.engine.authenticated_mono=time.monotonic()
    state.operation=None
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.started==[False] and s.operations.get('armed')['status']=='succeeded'
    await s.operations.stop()
    s.engine.state=state.model_copy(update={'lifecycle':'ended'})
    await finish(s,request('armed-again','arm',seconds=10))
    await s.operations.stop('user_stop')
    s.engine.state=state
    await asyncio.sleep(.12)
    assert s.operations.get('armed-again')['status']=='cancelled' and len(s.started)==1
    await s.client.close()


@pytest.mark.parametrize('change',['escape','generation'])
async def test_arm_revoked_by_escape_or_connection_generation(service,change):
    s=service
    await finish(s,request('waiting','arm',seconds=10))
    if change=='escape':
        s.executor.mouse.stop.set()
    else:
        s.engine.lobby_session.generation+=1
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.operations.get('waiting')['status']=='failed' and not s.started
    await s.client.close()


async def test_restart_revokes_pending_and_does_not_replay(service):
    s=service
    await finish(s,request('waiting-before-restart','arm',seconds=10))
    # A real restart terminates the old loop; cancel it before simulating startup.
    s.operations.arm_task.cancel()
    await asyncio.gather(s.operations.arm_task,return_exceptions=True)
    s.operations.update('waiting-before-restart',status='waiting_game')
    restarted=Operations(s)
    assert restarted.get('waiting-before-restart')['status']=='interrupted'
    assert not restarted.arm_id and not s.started
    await s.client.close()


async def test_settings_persist_without_refilling_pool_or_granting_validation(service):
    s=service
    s.coordinator.bank.remaining=3
    result=await finish(s,request('configure','settings',patch={'bank_seconds':100,'memory_recall_enabled':False,'review_enabled':False}))
    assert result['status']=='succeeded' and s.coordinator.bank.remaining==3
    loaded=Settings.load(s.settings.runtime/'private.json')
    assert not loaded.review_enabled and not loaded.memory_recall_enabled
    assert loaded.operation_token==s.settings.operation_token
    result=await finish(s,request('unproven','settings',patch={'stream_actions':True}))
    assert result['status']=='failed' and not s.settings.stream_actions
    for patch in ({'timer_verified':True},{'api_key':'private'},{'target_retries':True},{'memory_recall_limit':9}):
        with pytest.raises(ValidationError):
            request('forbidden','settings',patch=patch)
    await s.client.close()


@pytest.mark.parametrize('armed',[False,True])
async def test_same_input_mode_operation_preserves_play_or_prepared_grant(service,armed):
    s=service
    if armed:
        await finish(s,request('mode-arm','arm',seconds=10))
    else:
        s.coordinator.enabled=True
    original=(s.operations.arm_id,s.operations.arm_task,s.operations.arm_deadline,s.operations.epoch)
    result=await finish(s,request('same-mode','input_mode',mode='browser_background'))
    assert result['status']=='succeeded' and result['result']['unchanged']
    assert (s.operations.arm_id,s.operations.arm_task,s.operations.arm_deadline,s.operations.epoch)==original
    assert not s.stopped and not s.executor.mouse.stop.is_set()
    await s.operations.stop('fixture_finished')
    await s.client.close()


@pytest.mark.parametrize('mode',['browser_background','physical_foreground','invalid'])
async def test_local_input_mode_noop_validation_and_actual_switch(service,state,mode):
    s=service
    s.engine.state=state
    s.coordinator.enabled=True
    original_epoch=s.operations.epoch
    transport=httpx.ASGITransport(app=create_app(s.settings,s))
    async with httpx.AsyncClient(transport=transport,base_url='http://testserver') as client:
        response=await client.post('/control/input-mode',json={'mode':mode},
            headers={'Authorization':'Bearer '+s.settings.control_token.get_secret_value()})
    if mode=='physical_foreground':
        assert response.status_code==200 and not s.coordinator.enabled
        assert s.stopped==['input_mode_changed'] and s.executor.input_mode==mode
    else:
        assert response.status_code==(200 if mode=='browser_background' else 400)
        assert s.coordinator.enabled and not s.stopped and s.operations.epoch==original_epoch
    await s.client.close()


async def test_profile_switch_replaces_both_clients_and_hides_private_identity(service):
    s=service
    s.settings.compact_validated=True
    profiles=ModelProfiles(s.settings)
    profiles.capture('fixture-profile-baseline')
    data=json.loads(profiles.path.read_text(encoding='utf-8'))
    data['profiles']['fixture-private-alternative']={**data['profiles']['fixture-profile-baseline'],
        'model':'private-model-identity','base_url':'https://private-provider.invalid/v1','api_key':'private-other-key'}
    profiles.path.write_text(json.dumps(data),encoding='utf-8')
    old=s.client
    result=await finish(s,request('switch','profile_select',profile_id=profile_id('fixture-private-alternative')))
    assert result['status']=='succeeded'
    assert s.client is s.reviewer.client is s.coordinator.model and s.client is not old and old.http.is_closed
    assert s.settings.model=='private-model-identity' and not s.settings.compact_validated
    public=json.dumps([result,s.operations.profiles(),s.operations.settings(),s.operations.status()])
    for secret in ('private-model-identity','private-provider.invalid','private-other-key','fixture-private-alternative'):
        assert secret not in public
    await s.client.close()


async def test_api_capability_separation_and_snapshot_privacy(service,state):
    s=service
    client=TestClient(create_app(s.settings,s))
    def auth(token):
        return {'Authorization':'Bearer '+token.get_secret_value()}
    for token in (s.settings.read_token,s.settings.navigation_token):
        assert client.get('/api/v1/mahjong/operations/status',headers=auth(token)).status_code==401
    token=s.settings.operation_token
    assert client.get('/control/status',headers=auth(token)).status_code==401
    s.engine.state=state
    r=client.get('/api/v1/mahjong/operations/status',headers=auth(token))
    assert r.status_code==200 and 'hand' not in r.json() and 'plan' not in r.json()
    assert client.get('/api/v1/mahjong/operations/view/screenshot',headers=auth(token)).status_code==409
    assert client.get('/api/v1/mahjong/operations/catalog',headers=auth(token)).status_code==200
    assert 'operation_token' not in s.settings.model_dump()
    await s.client.close()


@pytest.mark.parametrize('change',['incomplete','unsupported','historical','expired'])
async def test_current_start_refuses_untrusted_or_expired_state(service,state,change):
    s=service
    s.engine.state=state
    if change=='incomplete':
        state.round_completeness='partial'
    elif change=='unsupported':
        state.rules.unsupported=['fixture']
    elif change=='historical':
        state.operation.historical=True
    else:
        state.operation.received_mono=time.monotonic()-1000
    result=await finish(s,request('refuse','start'))
    assert result['status']=='failed' and not s.started
    await s.client.close()


async def test_memory_revision_needs_actual_ended_evidence_and_does_not_mutate_snapshot(service,state):
    s=service
    state.lifecycle='ended'
    event=Event(id='fixture-source',match_id=state.match_id,seq=1,kind='ActionNewRound',data={},
                received_at='2026-01-01T00:00:00+00:00',received_mono=1,source='fixture')
    s.store.save_event(event,state)
    before=s.store.latest(state.match_id).model_dump()
    result=await finish(s,request('lesson','memory_revise',match_id=state.match_id,
        proposal={'claim':'领先且无安全进攻路线时，优先保位并检查对手威胁。','supports':[event.id]}))
    assert result['status']=='succeeded'
    mid=result['result']['id']
    assert Memories(s.store).get(mid)['status']=='candidate'
    bad=await finish(s,request('bad-lesson','memory_revise',match_id=state.match_id,
        proposal={'claim':'invalid source','supports':['not-observed']}))
    assert bad['status']=='failed' and s.store.latest(state.match_id).model_dump()==before
    await s.client.close()


async def test_teaching_command_persistence_idempotence_versions_and_private_reads(service):
    s=service
    s.coordinator.enabled=True  # Teaching updates do not require interrupting gameplay.
    command=request('teach-now','knowledge_write',source_quote='领先时检查防守收益',
        proposal={'claim':'领先低价值手面对立直时比较进攻和防守收益','topic':'defense',
                  'conditions':['高价值好型听牌仍应比较进攻收益']})
    result=await finish(s,command)
    tip=result['result']['knowledge']
    assert result['status']=='succeeded' and tip['trust']=='taught_unverified'
    assert (await s.operations.submit(command))==result
    assert s.store.db.execute('SELECT COUNT(*) FROM mahjong_knowledge').fetchone()[0]==1
    assert not s.store.latest()  # No invented match or event is required.
    view=s.operations.resource('knowledge',ids=[tip['id']])
    assert len(view['items'])==1 and 'source_digest' not in view['items'][0]
    conflict=await finish(s,request('stale-teaching','knowledge_write',source_quote='停用旧原则',
        proposal={'id':tip['id'],'revision':9,'status':'inactive'}))
    assert conflict['status']=='failed'
    for cid,proposal in [('disable-tip',{'id':tip['id'],'revision':1,'status':'inactive'}),
                         ('edit-disabled-tip',{'id':tip['id'],'revision':2,'claim':'领先仍应比较具体收益'}),
                         ('restore-tip',{'id':tip['id'],'revision':3,'status':'active'})]:
        edited=await finish(s,request(cid,'knowledge_write',source_quote='修订这条麻将技巧',proposal=proposal))
        assert edited['status']=='succeeded'
        assert edited['result']['knowledge']['status']==('active' if cid=='restore-tip' else 'inactive')
    assert s.operations.resource('learning')['active_teachings']==1
    assert len(s.operations.resource('knowledge',ids=[tip['id']])['revisions'])==4
    await s.client.close()


async def test_disable_experience_without_inventing_match_keeps_evidence_and_rejects_conflicts(service,state):
    s=service
    state.lifecycle='ended'
    event=Event(id='actual-source',match_id=state.match_id,seq=1,kind='ActionNewRound',data={},
                received_at='2026-01-01T00:00:00+00:00',received_mono=1,source='fixture')
    s.store.save_event(event,state)
    memory=Memories(s.store).revise({'claim':'需要重新检验的旧经验','supports':[event.id]},state.match_id)
    disabled=await finish(s,request('disable-experience','memory_disable',id=memory['id'],revision=1,
        reason='适用边界不足，等待真实对局再检验',source_quote='先停用旧经验'))
    assert disabled['status']=='succeeded'
    current=Memories(s.store).get(memory['id'])
    assert current['status']=='inactive' and current['supports']==memory['supports']
    assert current['origin_match']==memory['origin_match']
    stale=await finish(s,request('stale-experience','memory_disable',id=memory['id'],revision=1,
        reason='旧版本',source_quote='先停用旧经验'))
    assert stale['status']=='failed' and len(Memories(s.store).history(memory['id']))==2
    assert 'source_quote' not in s.store.records('memory_admin_disable')[0]['payload']
    await s.client.close()


async def test_escape_during_arm_binding_is_not_cleared(service,state):
    s=service
    state.operation=None
    async def bind(*,clear_stop=True):
        assert not clear_stop
        s.executor.mouse.stop.set()
    s.executor.bind_dedicated=bind
    await finish(s,request('arm-then-escape','arm',seconds=10))
    s.engine.authenticated_mono=time.monotonic()
    s.engine.state=state
    await asyncio.wait_for(s.operations.arm_task,1)
    assert not s.started and s.operations.get('arm-then-escape')['status']=='failed'
    await s.client.close()


async def test_room_uncertainty_propagates_without_duplicate_input(service):
    s=service
    s.engine.lobby_session.room={'room_code':'12345','playing':False,'humans':4,'robots':0,'players':4,'mode':{'mode':2}}
    async def click(scene,control,ctx,*,submit=None):
        ctx.before_submit(submit)
    s.lobby_ui.click=click
    from majsoul_llm.practice import PracticeRooms
    original=PracticeRooms.ACK_TIMEOUT
    PracticeRooms.ACK_TIMEOUT=0
    try:
        r=request('room-unknown','room',room_code='12345',operation='ready')
        result=await finish(s,r)
        assert result['status']=='uncertain'
        assert (await s.operations.submit(r))==result
        assert len(s.store.records('room_command'))==2
    finally:
        PracticeRooms.ACK_TIMEOUT=original
        await s.client.close()


@pytest.mark.parametrize('escape_during_bind',[False,True])
@pytest.mark.parametrize('requested_takeover',[False,True])
async def test_room_start_takeover_uses_verified_handoff_and_preserves_escape(service,state,escape_during_bind,requested_takeover):
    s=service
    session=s.engine.lobby_session
    session.room={'room_code':'12345','playing':False,'humans':4,'robots':0,'players':4,'mode':{'mode':2}}
    state.operation=None
    async def click(scene,control,ctx,*,submit=None):
        assert s.operations.arm_id, 'takeover must be armed before room start is clicked'
        assert s.operations.get(s.operations.arm_id)['status']=='waiting_game'
        ctx.before_submit(submit)
        session.latest[submit]={'observed_mono':time.monotonic(),'error_code':0}
        session.room['playing']=True
        s.engine.state=state
        s.engine.authenticated_mono=time.monotonic()
    async def bind(*,clear_stop=True):
        assert not clear_stop
        if escape_during_bind:
            s.executor.mouse.stop.set()
    s.lobby_ui.click=click
    s.executor.bind_dedicated=bind
    result=await finish(s,request('room-start-handoff','room',room_code='12345',operation='start',takeover=requested_takeover))
    if escape_during_bind:
        assert result['status']=='failed' and not s.started
        assert s.executor.mouse.stop.is_set()
        assert not s.store.records('room_command')  # Esc prevents even the start press.
    else:
        assert result['status']=='succeeded' and result['result']['takeover']=='active'
        assert s.started==[False]
        assert len(s.store.records('room_command')) >= 2
    await s.client.close()


@pytest.mark.parametrize('arm_after_auth',[False,True])
async def test_arm_waits_through_real_auth_before_first_round(service,state,arm_after_auth):
    s=service
    engine=Engine(s.store)
    engine.lobby_session=s.engine.lobby_session
    s.engine=engine
    async def authenticate():
        await engine.message({'method':'.lq.FastTest.authGame','direction':'request','connection':'fixture',
            'data':{'account_id':101,'game_uuid':'test'}})
        await engine.message({'method':'.lq.FastTest.authGame','direction':'response','connection':'fixture',
            'data':{'seat_list':[101,102,103,104],'game_config':{'mode':{'mode':2}}}})
    if arm_after_auth:
        await authenticate()
    command=request('wait-for-first-round','arm',seconds=10)
    result=await finish(s,command)
    assert result['status']=='waiting_game'
    if not arm_after_auth:
        await authenticate()
    await asyncio.sleep(.15)
    assert s.operations.get(command.command_id)['status']=='waiting_game'
    assert not s.started and not s.coordinator.enabled
    await engine.action('ActionNewRound',{'tiles':state.hand,'scores':[25000]*4,'doras':['9p']},0)
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.operations.get(command.command_id)['status']=='succeeded'
    assert s.started==[False]
    await s.client.close()


async def test_arm_waits_for_next_fresh_action_without_refilling_time(service,state):
    s=service
    await finish(s,request('wait-clock','arm',seconds=10))
    state.operation.received_mono=time.monotonic()-1000
    s.engine.state=state
    s.engine.authenticated_mono=time.monotonic()
    s.coordinator.bank.remaining=2
    await asyncio.sleep(.15)
    assert not s.started and s.operations.get('wait-clock')['status']=='waiting_game'
    state.operation.received_mono=time.monotonic()
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.started==[False] and s.coordinator.bank.remaining==2
    await s.client.close()


async def test_partial_handoff_stop_is_final_even_after_round_arrives(service,state):
    s=service
    s.engine.state=state
    state.operation=None
    state.round_completeness='partial'
    await finish(s,request('partial-stop','arm',seconds=10))
    assert s.operations.startup()['phase']=='awaiting_complete_round'
    await s.operations.submit(request('explicit-stop','stop'))
    state.round_completeness='full'
    await asyncio.sleep(.15)
    assert not s.started and s.operations.get('partial-stop')['status']=='cancelled'
    assert s.executor.mouse.stop.is_set()
    await s.client.close()


@pytest.mark.parametrize('change',['session','authentication'])
async def test_binding_requires_the_same_authentication(service,state,change):
    s=service
    s.engine.state=state
    state.operation=None
    async def bind(**kwargs):
        if change=='session':
            s.engine.lobby_session.generation+=1
        else:
            s.engine.authenticated_mono+=1
    s.executor.bind_dedicated=bind
    result=await finish(s,request('changed-authentication','start'))
    assert result['reason']=='authentication_changed_during_binding' and not s.started
    await s.client.close()


@pytest.mark.parametrize('expiry_stage',['waiting','binding'])
async def test_arm_deadline_covers_waiting_and_binding_without_refresh(service,state,monkeypatch,expiry_stage):
    import majsoul_llm.operations as operations_module
    s=service
    clock=SimpleNamespace(now=time.monotonic())
    monkeypatch.setattr(operations_module,'time',SimpleNamespace(monotonic=lambda:clock.now))
    s.engine.state=state
    state.operation=None
    state.round_completeness='partial'
    await finish(s,request('original-deadline','arm',seconds=10))
    async def bind(**kwargs):
        clock.now+=11
    s.executor.bind_dedicated=bind
    if expiry_stage=='binding':
        state.round_completeness='full'
    else:
        clock.now+=11
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.operations.get('original-deadline')['status']=='expired' and not s.started
    await s.client.close()


async def test_expiry_during_binding_waits_for_fresh_facts(service,state):
    s=service
    await finish(s,request('expires-in-binding','arm',seconds=10))
    s.engine.state=state
    s.engine.authenticated_mono=time.monotonic()
    state.operation.received_mono=time.monotonic()
    bindings=[]
    async def bind(**kwargs):
        bindings.append(True)
        if len(bindings)==1:
            state.operation.received_mono-=1000
    s.executor.bind_dedicated=bind
    await asyncio.sleep(.15)
    assert not s.started and len(bindings)==1
    assert s.operations.startup()['phase']=='awaiting_fresh_action'
    state.operation.received_mono=time.monotonic()
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.started==[False] and len(bindings)==2
    await s.client.close()


async def test_waiting_cannot_adopt_stale_auth_or_a_different_second_match(service,state):
    s=service
    await finish(s,request('one-auth-one-match','arm',seconds=10))
    s.engine.state=state
    state.operation=None
    state.round_completeness='partial'
    await asyncio.sleep(.15)
    assert s.operations.startup()['phase']=='awaiting_new_game'
    s.engine.authenticated_mono=time.monotonic()
    await asyncio.sleep(.15)
    assert s.operations.startup()['phase']=='awaiting_complete_round'
    s.engine.state=state.model_copy(update={'match_id':'other-match','round_completeness':'full'})
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.operations.get('one-auth-one-match')['reason']=='match_changed_while_waiting'
    assert not s.started
    await s.client.close()


@pytest.mark.parametrize('action',['start','shadow'])
async def test_panel_current_start_uses_durable_automatic_binding(service,state,action):
    s=service
    s.engine.state=state
    state.operation=None
    s.executor.window=None
    bindings=[]
    async def bind(*,clear_stop=True):
        bindings.append(clear_stop)
        s.executor.window=object()
    s.executor.bind_dedicated=bind
    transport=httpx.ASGITransport(app=create_app(s.settings,s))
    async with httpx.AsyncClient(transport=transport,base_url='http://testserver') as client:
        for token in (s.settings.read_token,s.settings.navigation_token,s.settings.operation_token):
            denied=await client.post('/control/'+action,json={'command_id':'panel-current'},
                headers={'Authorization':'Bearer '+token.get_secret_value()})
            assert denied.status_code==401
        headers={'Authorization':'Bearer '+s.settings.control_token.get_secret_value()}
        response=await client.post('/control/'+action,json={'command_id':'panel-current'},headers=headers)
        assert response.status_code==200 and response.json()['request']['command']['action']==action
        await s.operations.task
        result=s.operations.get('panel-current')
        assert result['status']=='succeeded' and bindings==[False]
        assert s.started==[action=='shadow']
        repeated=await client.post('/control/'+action,json={'command_id':'panel-current'},headers=headers)
        assert repeated.json()==result and len(bindings)==1
    await s.client.close()


async def test_panel_lobby_start_prepares_autoplay_and_can_be_stopped(service):
    s=service
    transport=httpx.ASGITransport(app=create_app(s.settings,s))
    async with httpx.AsyncClient(transport=transport,base_url='http://testserver') as client:
        headers={'Authorization':'Bearer '+s.settings.control_token.get_secret_value()}
        await client.post('/control/start',json={'command_id':'lobby-start'},headers=headers)
        await s.operations.task
        prepared=s.operations.get('lobby-start')
        assert prepared['status']=='waiting_game' and prepared['result']['autoplay_prepared']
        assert not s.started
        await client.post('/control/stop',json={},headers=headers)
        assert s.operations.get('lobby-start')['status']=='cancelled'
        await client.post('/control/arm',json={'command_id':'panel-wait','seconds':10},headers=headers)
        await s.operations.task
        status=await client.get('/api/v1/mahjong/operations/status',headers=headers)
        assert status.json()['startup']['phase']=='awaiting_new_game'
        assert not status.json()['enabled']
        await client.post('/control/stop',json={},headers=headers)
        assert s.operations.get('panel-wait')['status']=='cancelled' and not s.started
    await s.client.close()


async def test_room_start_reuses_existing_arm_before_press_without_extending_it(service,state):
    s=service
    session=s.engine.lobby_session
    session.room={'room_code':'12345','playing':False,'humans':1,'robots':0,'players':1,'mode':{'mode':3}}
    await finish(s,request('original-room-arm','arm',seconds=10))
    original_task=s.operations.arm_task
    original_created=s.operations.get('original-room-arm')['created_at']
    state.operation=None
    presses=[]
    async def click(scene,control,ctx,*,submit=None):
        assert s.operations.arm_id=='original-room-arm' and s.operations.arm_task is original_task
        assert s.operations.get('original-room-arm')['request']['command']['seconds']==10
        assert s.operations.get('original-room-arm')['created_at']==original_created
        assert s.store.records('room_takeover_prepared')
        ctx.before_submit(submit)
        presses.append(control)
        session.latest[submit]={'observed_mono':time.monotonic(),'error_code':0}
        session.room['playing']=True
        s.engine.authenticated_mono=time.monotonic()
        s.engine.state=state
    s.lobby_ui.click=click
    command=request('armed-room-start','room',room_code='12345',operation='start',takeover=True)
    result=await finish(s,command)
    assert result['status']=='succeeded' and result['result']['takeover']=='active'
    assert presses==['start'] and s.started==[False]
    assert (await s.operations.submit(command))==result and presses==['start']
    await s.client.close()


@pytest.mark.parametrize('failure',['invisible','rejected','unknown','stopped','expired_before_press'])
async def test_prearmed_room_start_failure_and_uncertainty_do_not_repress(service,monkeypatch,failure):
    from majsoul_llm.practice import PracticeRooms
    s=service
    session=s.engine.lobby_session
    session.room={'room_code':'12345','playing':False,'humans':1,'robots':0,'players':1,'mode':{'mode':3}}
    presses=[]
    async def click(scene,control,ctx,*,submit=None):
        assert s.operations.arm_id
        assert s.operations.get(s.operations.arm_id)['status']=='waiting_game'
        if failure=='invisible':
            raise LobbyError('lobby_control_not_visible_or_available')
        if failure=='stopped':
            s.executor.mouse.stop.set()
            ctx.check()
        if failure=='expired_before_press':
            s.operations.arm_deadline=time.monotonic()-1
        ctx.before_submit(submit)
        presses.append(control)
        if failure=='rejected':
            session.latest[submit]={'observed_mono':time.monotonic(),'error_code':1001}
    s.lobby_ui.click=click
    monkeypatch.setattr(PracticeRooms,'ACK_TIMEOUT',.01)
    command=request('failed-prearmed-start','room',room_code='12345',operation='start',takeover=True)
    result=await finish(s,command)
    assert not s.started
    if failure=='unknown':
        assert result['status']=='uncertain' and result['result']['autoplay_prepared']
        assert s.operations.arm_id
        await s.operations.stop('test_stop')
    else:
        assert result['status']=='failed' and not s.operations.arm_id
    assert (await s.operations.submit(command))==result
    assert len(presses)==(1 if failure in ['rejected','unknown'] else 0)
    await s.client.close()


async def test_room_change_revokes_prepared_grant_without_starting_another_room(service):
    s=service
    s.engine.lobby_session.room={'room_code':'12345','playing':False,'mode':{'mode':3}}
    from majsoul_llm.practice import RoomAction
    cid=await s.operations.prepare_room_start(RoomAction(command_id='bind-room',room_code='12345',action='start',takeover=True))
    assert s.operations.get(cid)['status']=='waiting_game'
    s.engine.lobby_session.room={'room_code':'99999','playing':False,'mode':{'mode':3}}
    await asyncio.wait_for(s.operations.arm_task,1)
    assert s.operations.get(cid)['reason']=='room_changed_while_waiting' and not s.started
    await s.client.close()


async def test_first_action_uses_real_coordinator_before_room_start_response(service,state):
    s=service
    session=s.engine.lobby_session
    session.room={'room_code':'12345','playing':False,'humans':1,'robots':0,'players':1,'mode':{'mode':3}}
    engine=Engine(s.store)
    engine.lobby_session=session
    s.engine=engine
    order=[]
    first_input=asyncio.Event()
    class Model:
        async def decide(self,context,deadline):
            assert 'startRoom' not in session.latest
            order.append('model_first_window')
            decision,action=context.validate({'q':context.q,'a':next(iter(context.actions)),'r':'fixture','p':None})
            return decision,action,{}
    async def bind(*,clear_stop=True):
        assert not clear_stop
        order.append('bind')
    async def execute(actual,action,deadline):
        assert 'startRoom' not in session.latest and s.input_lease.owner is None
        assert actual.operation and not actual.operation.historical and deadline>time.monotonic()
        order.append('first_input')
        first_input.set()
        return time.monotonic()
    s.executor.bind_dedicated=bind
    s.executor.execute=execute
    s.executor.lease=s.input_lease
    s.coordinator=Coordinator(s.settings,engine,Model(),s.executor)
    engine.on_state=s.coordinator.changed
    async def click(scene,control,ctx,*,submit=None):
        assert s.operations.get(s.operations.arm_id)['status']=='waiting_game'
        assert order==['bind']
        ctx.before_submit(submit)
        order.append('start_press')
        session.room['playing']=True
        await engine.message({'method':'.lq.FastTest.authGame','direction':'request','connection':'fixture',
            'data':{'account_id':101,'game_uuid':'test'}})
        await engine.message({'method':'.lq.FastTest.authGame','direction':'response','connection':'fixture',
            'data':{'seat_list':[101,102,103,104],'game_config':{'mode':{'mode':3}}}})
        await engine.action('ActionNewRound',{'tiles':state.hand,'scores':[25000]*4,'doras':['9p'],
            'operation':{'seat':0,'operation_list':[{'type':1,'combination':[]}]}},0)
    s.lobby_ui.click=click
    async def late_ack():
        await asyncio.wait_for(first_input.wait(),2)
        session.latest['startRoom']={'observed_mono':time.monotonic(),'error_code':0}
        order.append('start_response')
    ack_task=asyncio.create_task(late_ack())
    try:
        result=await finish(s,request('prearmed-first-window','room',room_code='12345',operation='start',takeover=True))
        await ack_task
        assert result['status']=='succeeded' and result['result']['takeover']=='active'
        assert order.index('first_input')<order.index('start_response')
        assert len(s.store.records('decision'))==1 and not s.store.records('decision_failure')
    finally:
        ack_task.cancel()
        await asyncio.gather(ack_task,return_exceptions=True)
        await s.operations.stop('test_finished')
        await s.client.close()


@pytest.mark.parametrize('gate',['missing_key','shadow_grant','shadow_start','expired_grant','changed_room'])
async def test_unprepared_room_start_never_presses(service,gate):
    s=service
    session=s.engine.lobby_session
    session.room={'room_code':'12345','playing':False,'humans':1,'robots':0,'players':1,'mode':{'mode':3}}
    if gate=='missing_key':
        s.settings.api_key=SecretStr('')
    if gate=='shadow_grant':
        await finish(s,request('shadow-before-start','arm',shadow=True,seconds=10))
    if gate=='shadow_start':
        await finish(s,request('shadow-start-before-room','shadow'))
    if gate=='expired_grant':
        await finish(s,request('expiring-before-start','arm',seconds=10))
    async def bind(**kwargs):
        if gate=='expired_grant':
            await s.operations.revoke_arm('test_expired')
        if gate=='changed_room':
            session.room['room_code']='99999'
    s.executor.bind_dedicated=bind
    async def click(*args,**kwargs):
        raise AssertionError('unprepared start must not click')
    s.lobby_ui.click=click
    result=await finish(s,request('unprepared-room','room',room_code='12345',operation='start',takeover=True))
    assert result['status']=='failed' and result['reason']!='operation_unavailable_assertionerror'
    assert not s.started and not s.store.records('room_command')
    await s.operations.stop('cleanup')
    await s.client.close()


async def test_local_room_start_also_prepares_before_press_with_legacy_false_flag(service,state):
    from majsoul_llm.practice import PracticeRooms
    s=service
    s.practice=PracticeRooms(s)
    session=s.engine.lobby_session
    session.room={'room_code':'12345','playing':False,'humans':1,'robots':0,'players':1,'mode':{'mode':3}}
    state.operation=None
    async def click(scene,control,ctx,*,submit=None):
        assert s.operations.get(s.operations.arm_id)['status']=='waiting_game'
        ctx.before_submit(submit)
        session.latest[submit]={'observed_mono':time.monotonic(),'error_code':0}
        session.room['playing']=True
        s.engine.state=state
        s.engine.authenticated_mono=time.monotonic()
    s.lobby_ui.click=click
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(s.settings,s)),base_url='http://testserver') as client:
        response=await client.post('/control/room',headers={'Authorization':'Bearer '+s.settings.control_token.get_secret_value()},
            json={'command_id':'local-prearmed','room_code':'12345','action':'start','takeover':False})
        assert response.status_code==200 and response.json()['takeover']=='active'
        assert s.started==[False]
    await s.client.close()
