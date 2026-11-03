import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from majsoul_llm.app import create_app
from majsoul_llm.config import Settings
from majsoul_llm.domain import Event
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
async def test_room_start_takeover_uses_verified_handoff_and_preserves_escape(service,state,escape_during_bind):
    s=service
    session=s.engine.lobby_session
    session.room={'room_code':'12345','playing':False,'humans':4,'robots':0,'players':4,'mode':{'mode':2}}
    state.operation=None
    async def click(scene,control,ctx,*,submit=None):
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
    result=await finish(s,request('room-start-handoff','room',room_code='12345',operation='start',takeover=True))
    if escape_during_bind:
        assert result['status']=='failed' and not s.started
        assert s.executor.mouse.stop.is_set()
    else:
        assert result['status']=='succeeded' and result['result']['takeover']=='active'
        assert s.started==[False]
    assert len(s.store.records('room_command')) >= 2
    await s.client.close()
