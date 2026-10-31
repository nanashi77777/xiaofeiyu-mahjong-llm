import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.domain import Phase
from majsoul_llm.engine import Engine
from majsoul_llm.execution import Executor
from majsoul_llm.input_lease import InputLease
from majsoul_llm.native import Window
from majsoul_llm.round_flow import RoundFlow
from majsoul_llm.queries import Queries


async def test_recovered_current_clock_subtracts_calibrated_age_and_never_refills_saved_bank(store,state):
    from majsoul_llm.timing import TimeBank
    from majsoul_llm.domain import Snapshot
    from conftest import HAND
    engine = Engine(store,recovery_timer_verified=True)
    engine.state = Snapshot(match_id='test',self_seat=0)
    data = {'tiles':HAND,'scores':[25000]*4,'operation':{'seat':0,'time_fixed':5000,'time_add':10000,
             'operation_list':[{'type':1}]}}
    actions = [{'name':'ActionNewRound','step':0,'data':data}]
    await engine.restore({'actions':actions,'passed_waiting_time':6})
    op = engine.state.operation
    assert op and not op.historical and op.timer_verified
    assert 7 <= time.monotonic()-op.received_mono < 8
    bank = TimeBank(bank=10)
    bank.enter_round(engine.state.round_uid)
    assert 6 < bank.deadline(op,verified=True)-time.monotonic() < 8
    bank.remaining = 2
    with pytest.raises(TimeoutError):
        bank.deadline(op,verified=True)
    assert bank.remaining == 2
    await engine.restore({'actions':actions,'passed_waiting_time':20})
    assert engine.state.operation is None
    assert len(store.events('test')) == 1


async def test_recorded_window_is_not_redecided_after_coordinator_restart(store,state):
    claimed = asyncio.Event()
    class Model:
        async def decide(self,*args):
            claimed.set()
            await asyncio.Event().wait()
    settings = Settings(api_key=SecretStr('fixture-credential'))
    engine = SimpleNamespace(store=store,state=state)
    first = Coordinator(settings,engine,Model())
    await first.start()
    await asyncio.wait_for(claimed.wait(),1)
    await first.stop()
    assert store.records('decision_window',state.match_id,1)
    second = Coordinator(settings,engine,Model())
    await second.start()
    assert not second.active() and second.task is None


async def test_only_prepress_expiry_can_reobserve_same_semantic_target(state):
    from majsoul_llm.execution import Target
    from majsoul_llm.native import InputDeferred, TargetExpired
    from majsoul_llm.rules import legal_actions
    action = legal_actions(state)[0]
    executor = object.__new__(Executor)
    executor.state_getter = lambda:state
    executor.input_mode,executor.metrics = 'browser_background',[]
    executor.mouse = SimpleNamespace(stop=threading.Event(),user=SimpleNamespace(GetAsyncKeyState=lambda key:0))
    targets,presses = [],[]
    async def target(semantic,s,indices,deadline=None):
        targets.append((semantic,indices))
        return Target((10,10),(0,0,100,100),time.monotonic(),'test',(10,10),{'test':True})
    async def layout():
        return {'test':True}
    async def click(point,deadline,stop,validate):
        if len(targets) == 1:
            raise TargetExpired('nothing pressed')
        await validate()
        presses.append(point)
        return time.monotonic()
    executor.target = target
    executor.browser = SimpleNamespace(layout_metrics=layout,background_click=click)
    await executor._execute(state,action,time.monotonic()+3)
    assert targets == [('tile',[action.tile_index])]*2 and presses == [(10,10)]
    async def uncertain(*args,**kwargs):
        raise InputDeferred('possibly pressed')
    executor.browser.background_click = uncertain
    targets.clear()
    with pytest.raises(InputDeferred,match='possibly pressed'):
        await executor._execute(state,action,time.monotonic()+3)
    assert len(targets)==1


async def test_preobserved_targets_require_same_window_version_and_freshness(state):
    from majsoul_llm.execution import Target
    executor=object.__new__(Executor)
    executor.metrics=[]
    executor.state_getter=lambda:state
    executor.mouse=SimpleNamespace(stop=threading.Event())
    cached=Target((1,2),(0,0,0,0),time.monotonic(),'parallel_visible_observation',(1,2),{})
    key=(state.match_id,state.state_version,state.operation.window_id)
    executor.observed_targets={key:{('tile',(3,)):cached}}
    calls=[]
    async def fresh(*args):
        calls.append(args)
        return Target((4,5),(0,0,0,0),time.monotonic(),'fresh')
    executor._target_once=fresh
    assert await executor.target('tile',state,[3]) is cached
    assert not calls
    changed=state.model_copy(deep=True)
    changed.operation.window_id='new-window'
    executor.state_getter=lambda:changed
    assert (await executor.target('tile',changed,[3])).evidence=='fresh'
    executor.state_getter=lambda:state
    cached.observed=time.monotonic()-1
    assert (await executor.target('tile',state,[3])).evidence=='fresh'
    assert len(calls)==2


async def test_target_observation_checks_deadline_and_state_before_retry(state):
    from majsoul_llm.execution import Target
    from majsoul_llm.native import InputDeferred
    executor=object.__new__(Executor)
    executor.state_getter=lambda:state
    executor.mouse=SimpleNamespace(stop=threading.Event())
    executor.metrics=[]
    calls=[]
    async def expired(*args):
        calls.append(args)
        state.operation=None
        return Target((1,2),(0,0,0,0),time.monotonic()-2,'stale')
    executor._target_once=expired
    with pytest.raises(TimeoutError,match='deadline expired'):
        await executor.target('tile',state,[0],time.monotonic()-1)
    assert not calls
    with pytest.raises(InputDeferred,match='superseded'):
        await executor.target('tile',state,[0],time.monotonic()+1)
    assert len(calls)==1


@pytest.mark.parametrize('model_fails',[False,True])
async def test_observation_releases_input_owner_before_model_selected_click(store,state,model_fails):
    from majsoul_llm.model import ModelFailure
    observed=asyncio.Event()
    lease=InputLease()
    pressed=[]
    class Model:
        async def decide(self,context,deadline):
            await observed.wait()
            if model_fails:
                raise ModelFailure('fixture channel failure')
            decision,action=context.validate({'q':context.q,'a':'a0','r':'保留进张','p':None})
            return decision,action,{}
    class FakeExecutor:
        mouse=SimpleNamespace(stop=threading.Event())
        async def observe(self,s,actions,deadline):
            async with lease.hold('observer'):
                observed.set()
                await asyncio.Event().wait()
        async def execute(self,s,action,deadline):
            assert lease.owner is None
            pressed.append(action)
            return time.monotonic()
    coordinator=Coordinator(Settings(integration_test=True),SimpleNamespace(store=store,state=state),Model(),FakeExecutor())
    coordinator.shadow=False
    await coordinator.decide(state)
    assert lease.owner is None
    assert len(pressed)==(0 if model_fails else 1)
    assert len(store.records('decision_failure'))==(1 if model_fails else 0)


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
