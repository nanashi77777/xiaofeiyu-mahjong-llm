"""Fault injection verifies retry budgets, press boundaries and stop priority."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from conftest import event
from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.execution import Executor, Target
from majsoul_llm.input_lease import InputLease
from majsoul_llm.model import ModelClient, ModelFailure
from majsoul_llm.native import InputDeferred, TargetExpired
from majsoul_llm.queries import Queries
from majsoul_llm.rules import legal_actions
from majsoul_llm.wire import build_context


def reply(context, *, stream=False):
    raw = {'a':'a0','r':'保留进张兼顾防守','p':None}
    if not stream:
        raw['q'] = context.q
    return ''.join('data: '+json.dumps(x)+'\n\n' for x in (
        {'type':'response.output_text.delta','delta':json.dumps(raw)},
        {'type':'response.completed','response':{'status':'completed',
          'usage':{'input_tokens':10,'output_tokens':5,'total_tokens':15}}}))


@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('fault',[401,403,429,503,'network','format','disconnect'])
async def test_model_attempts_are_shared_bounded_and_all_usage_retained(store,state,stream,fault):
    context = build_context(state,legal_actions(state))
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            if fault == 'network':
                raise httpx.ConnectError('fixture',request=request)
            if fault == 'format':
                return httpx.Response(200,text='data: {malformed}\n\n')
            if fault == 'disconnect':
                return httpx.Response(200,text='data: {"type":"response.created"}\n\n')
            return httpx.Response(fault)
        return httpx.Response(200,text=reply(context,stream=stream))
    model = ModelClient(Settings(stream_action_format='short'),store,httpx.MockTransport(handler))
    deadline = time.monotonic()+3
    permanent = fault in (401,403)
    try:
        if permanent:
            with pytest.raises(ModelFailure) as exc:
                await (model.begin_decision(context,deadline) if stream else model.decide(context,deadline))
            assert not exc.value.retryable
        elif stream:
            choice = await model.begin_decision(context,deadline)
            await choice.completion
            assert choice.action is context.actions['a0']
        else:
            _,action,_ = await model.decide(context,deadline)
            assert action is context.actions['a0']
        assert len(requests) == (1 if permanent else 2)
        usage = store.records('token_usage')
        assert len(usage) == len(requests)
        assert len({r['payload']['request_id'] for r in usage}) == len(requests)
        assert sorted(r['payload']['attempt'] for r in usage) == list(range(1,len(requests)+1))
        assert sum(r['payload']['usage'] is None for r in usage) == 1
        assert store.records('recovery')[0]['payload']['outcome'] == ('failed' if permanent else 'retry')
    finally:
        await model.close()


@pytest.mark.parametrize('attempts,remaining',[(1,3),(2,.1)])
async def test_model_cannot_nest_retries_or_extend_deadline(store,state,attempts,remaining):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503)
    client = ModelClient(Settings(model_attempts=attempts,stream_hedge_delay_seconds=.01),store,httpx.MockTransport(handler))
    context = build_context(state,legal_actions(state))
    # A one-request budget also prevents a hedged second request.
    client.settings.stream_hedge_delay_seconds = .01 if attempts == 1 else 0
    with pytest.raises(ModelFailure):
        await client.begin_decision(context,time.monotonic()+remaining)
    assert len(calls) == 1
    await client.close()


def executor_fixture(state,tmp_path):
    ex = object.__new__(Executor)
    ex.state_getter = lambda:state
    ex.input_mode,ex.metrics = 'browser_background',[]
    ex.lease = InputLease()
    ex.mouse = SimpleNamespace(stop=threading.Event(),user=SimpleNamespace(GetAsyncKeyState=lambda key:0))
    async def layout():
        return {'fixture':True}
    ex.browser = SimpleNamespace(settings=SimpleNamespace(runtime=tmp_path,target_retries=2),layout_metrics=layout)
    return ex


@pytest.mark.parametrize('fault',['before','after','after_target_expired'])
async def test_one_retry_budget_and_never_repeat_a_possible_press(state,tmp_path,fault):
    ex = executor_fixture(state,tmp_path)
    targets,presses = [],[]
    async def target(semantic,s,indices,deadline):
        targets.append((semantic,tuple(indices or []),deadline))
        return Target((10,10),(0,0,0,0),time.monotonic(),'fixture',(10,10),{'fixture':True})
    async def click(point,deadline,stop,validate,on_press):
        await validate()
        if fault == 'before':
            raise InputDeferred('animation before press')
        on_press()
        presses.append(point)
        raise (TargetExpired if fault == 'after_target_expired' else InputDeferred)('ambiguous mouse response')
    ex.target,ex.browser.background_click = target,click
    action = legal_actions(state)[0]
    deadline = time.monotonic()+2
    with pytest.raises(InputDeferred):
        await ex.execute(state,action,deadline)
    assert len(targets) == (3 if fault == 'before' else 1)
    assert len(set(targets)) == 1  # Same semantic action, indices and original deadline.
    assert len(presses) == (0 if fault == 'before' else 1)
    assert ex.press_may_have_occurred == (fault != 'before')


@pytest.mark.parametrize('failure',['animation','after_press'])
async def test_riichi_stage_is_not_reopened_or_repressed(state,tmp_path,failure):
    ex = executor_fixture(state,tmp_path)
    calls,presses = [],[]
    async def target(semantic,s,indices,deadline):
        calls.append(semantic)
        if semantic == 'riichi_tile' and failure == 'animation':
            raise InputDeferred('tile not yet visible')
        return Target((10,10),(0,0,0,0),time.monotonic(),'fixture',(10,10),{'fixture':True})
    async def click(point,deadline,stop,validate,on_press):
        await validate()
        on_press()
        presses.append(calls[-1])
        if calls[-1] == 'riichi_tile':
            raise InputDeferred('release unknown')
        return time.monotonic()
    ex.target,ex.browser.background_click = target,click
    action = legal_actions(state)[0].model_copy(update={'kind':'riichi'})
    with pytest.raises(InputDeferred):
        await ex.execute(state,action,time.monotonic()+2)
    assert calls.count('riichi') == 1
    assert calls.count('riichi_tile') == (3 if failure == 'animation' else 1)
    assert presses == (['riichi'] if failure == 'animation' else ['riichi','riichi_tile'])


class ChoiceModel:
    def __init__(self,fail=False):
        self.fail = fail
        self.calls = 0

    async def decide(self,context,deadline):
        self.calls += 1
        if self.fail:
            raise ModelFailure('fixture exhausted model attempts')
        decision,action = context.validate({'q':context.q,'a':'a0','r':'保留进张兼顾防守','p':None})
        return decision,action,{}


def coordinator_fixture(store,state,*,model=None,executor=None,**settings):
    engine = SimpleNamespace(store=store,state=state)
    c = Coordinator(Settings(confirmation_wait_seconds=.03,connection_wait_seconds=.02,**settings),
                    engine,model or ChoiceModel(),executor)
    c.enabled,c.shadow = True,executor is None
    c.bank.enter_round(state.round_uid)
    return c


async def test_failed_window_continues_only_on_new_window_and_three_unique_failures_trip(store,state):
    c = coordinator_fixture(store,state,model=ChoiceModel(fail=True))
    await c.changed(state,None)
    await c.task
    assert c.enabled and c.recovery.streak == 1
    await c.changed(state,None)
    assert c.model.calls == 1  # Same claimed window cannot get a new model budget.
    c.failed_window(state,'same failure reported twice')
    assert c.recovery.streak == 1
    for i in (2,3):
        fresh = state.model_copy(deep=True)
        fresh.operation.window_id = f'fresh-{i}'
        c.engine.state = fresh
        await c.changed(fresh,None)
        await c.task
        assert c.recovery.streak == i
    assert not c.enabled and c.status == 'paused_repeated_window_failures'
    assert len(store.records('decision_failure')) == 3
    assert c.model.calls == 3
    await c.stop()


async def test_only_actual_confirmation_resets_failed_streak(store,state):
    class Ex:
        mouse = SimpleNamespace(stop=threading.Event())
        async def execute(self,s,a,deadline):
            return time.monotonic()
    c = coordinator_fixture(store,state,executor=Ex())
    c.failed_window(state,'earlier failure')
    fresh = state.model_copy(deep=True)
    fresh.operation.window_id = 'next'
    c.engine.state = fresh
    await c.decide(fresh)
    assert c.recovery.streak == 1 and c.pending
    tile = fresh.hand[c.pending['receipt']['action']['tile_index']]
    c.confirm(fresh,event('ActionDiscardTile',{'seat':0,'tile':tile}))
    assert c.recovery.streak == 0 and c.recovery.recovered == 1
    assert store.records('recovery')[0]['payload']['outcome'] == 'recovered'
    await c.stop()


@pytest.mark.parametrize('result',['late_confirm','unknown','stop'])
async def test_possible_submission_waits_for_receipt_and_never_reexecutes(store,state,result):
    class Ex:
        mouse = SimpleNamespace(stop=threading.Event())
        press_may_have_occurred = True
        calls = 0
        async def execute(self,s,a,deadline):
            self.calls += 1
            raise InputDeferred('release unknown')
    ex = Ex()
    c = coordinator_fixture(store,state,executor=ex)
    await c.decide(state)
    assert c.enabled and c.pending and c.status == 'recovering_input_confirmation'
    await c.changed(state,None)
    assert ex.calls == 1
    if result == 'late_confirm':
        tile = state.hand[c.pending['receipt']['action']['tile_index']]
        c.confirm(state,event('ActionDiscardTile',{'seat':0,'tile':tile}))
        await c.confirmation_task
        assert c.enabled and c.pending is None
        assert store.records('receipt')[0]['payload']['status'] == 'server_confirmed'
    elif result == 'unknown':
        await c.confirmation_task
        assert not c.enabled and c.status == 'paused_unconfirmed_input'
        tile = state.hand[c.pending['receipt']['action']['tile_index']]
        c.confirm(state,event('ActionDiscardTile',{'seat':0,'tile':tile}))
        assert not c.enabled  # A late receipt cannot undo the automatic pause.
    else:
        await c.stop('user_stop')
        await asyncio.sleep(.05)
        assert not c.enabled and c.status == 'user_stop'
    assert ex.calls == 1 and c.model.calls == 1
    await c.stop()


async def test_definitely_unsent_input_can_wait_for_next_window(store,state):
    class Ex:
        mouse = SimpleNamespace(stop=threading.Event())
        press_may_have_occurred = False
        async def execute(self,s,a,deadline):
            raise InputDeferred('no recognized target before any press')
    c = coordinator_fixture(store,state,executor=Ex())
    await c.decide(state)
    assert c.enabled and c.pending is None and c.recovery.streak == 1
    assert c.status == 'recovering_next_window'
    assert store.records('receipt')[0]['payload']['status'] == 'expired'
    await c.stop()


@pytest.mark.parametrize('result',['restore','exhaust','stop'])
async def test_connection_observation_is_bounded_never_refills_or_replays(store,state,result):
    c = coordinator_fixture(store,state)
    c.bank.remaining = 1
    interrupted = state.model_copy(deep=True)
    interrupted.operation,interrupted.lifecycle = None,'interrupted'
    c.engine.state = interrupted
    await c.changed(interrupted,None)
    assert c.status == 'recovering_connection'
    if result == 'restore':
        restored = state.model_copy(deep=True)
        restored.operation.historical = True
        c.engine.state = restored
        await c.changed(restored,None)
        assert c.enabled and not c.active() and c.model.calls == 0
    elif result == 'stop':
        await c.stop('user_stop')
        await asyncio.sleep(.05)
        assert not c.enabled and c.status == 'user_stop'
    else:
        await c.connection_task
        assert not c.enabled and c.status == 'paused_connection_recovery_exhausted'
        attempts = [r for r in store.records('recovery') if r['payload']['outcome'] == 'waiting_actual_sync']
        assert len(attempts) == 2
    assert c.bank.remaining == 1 and c.model.calls == 0
    await c.stop()


async def test_permanent_model_errors_pause_immediately_and_qq_projection_is_safe(store,state):
    class Model:
        async def decide(self,*args):
            raise ModelFailure('model HTTP 401',retryable=False)
    c = coordinator_fixture(store,state,model=Model())
    await c.decide(state)
    assert not c.enabled and c.status == 'paused_model_configuration'
    q = Queries(store,'fixture',recovery_status=c.recovery.public)
    projection = q.live()['recovery']
    assert projection['failed_windows'] == 1
    assert not {'window_id','match_id','model','api_key','hand','plan'} & set(projection)
    await c.stop()


async def test_stop_cancels_a_retry_backoff_without_a_second_request(store,state):
    failed = asyncio.Event()
    calls = []
    def handler(request):
        calls.append(request)
        failed.set()
        return httpx.Response(429)
    model = ModelClient(Settings(stream_actions=True,stream_action_format='short'),store,httpx.MockTransport(handler))
    c = coordinator_fixture(store,state,model=model,stream_actions=True)
    c.task = asyncio.create_task(c.decide(state))
    await failed.wait()
    await c.stop('user_stop')
    await asyncio.sleep(.07)
    assert len(calls) == 1 and not c.enabled and c.status == 'user_stop'
    assert not model.streams
    await model.close()


async def test_esc_during_target_observation_never_retries_or_presses(state,tmp_path):
    ex = executor_fixture(state,tmp_path)
    targets,presses = [],[]
    async def target(*args):
        targets.append(True)
        ex.mouse.stop.set()
        raise InputDeferred('input stopped')
    async def press(*args,**kwargs):
        presses.append(True)
    ex.target,ex.browser.background_click = target,press
    with pytest.raises(InputDeferred,match='stopped'):
        await ex.execute(state,legal_actions(state)[0],time.monotonic()+2)
    assert len(targets) == 1 and not presses


async def test_retries_cannot_grant_more_of_the_same_round_time_bank(store,state):
    c = coordinator_fixture(store,state,model=ChoiceModel(fail=True),timer_verified=True)
    c.bank.remaining = 2
    state.operation.fixed_ms = 5000
    state.operation.received_mono = time.monotonic()-6
    c.failed_window(state,'expired')
    after = c.bank.remaining
    c.failed_window(state,'same error twice')
    assert 0 < after < 1.01 and c.bank.remaining == after
    interrupted = state.model_copy(deep=True)
    interrupted.lifecycle,interrupted.operation = 'interrupted',None
    c.engine.state = interrupted
    await c.changed(interrupted,None)
    restored = state.model_copy(deep=True)
    restored.operation.historical = True
    c.engine.state = restored
    await c.changed(restored,None)
    assert c.bank.remaining <= after
    await c.stop()
