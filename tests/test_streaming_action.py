import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from conftest import event
from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.model import ModelClient
from majsoul_llm.rules import legal_actions
from majsoul_llm.state import apply_event
from majsoul_llm.streaming import ActionPrefix, strict_json
from majsoul_llm.wire import build_context


def sse(value):
    return ('data: '+json.dumps(value,ensure_ascii=False)+'\n\n').encode()


def test_prefix_waits_for_complete_object_and_separator_at_every_split(state):
    context=build_context(state,legal_actions(state))
    text=json.dumps({'d':{'q':context.q,'a':'a0'}},separators=(',',':'))[:-1]+','
    for split in range(len(text)):
        prefix=ActionPrefix(context)
        assert prefix.feed(text[:split]) is None
        assert prefix.feed(text) is context.actions['a0']
        assert prefix.feed(text+'"r":"改选a1"') is None
    for d in ({'q':'wrong','a':'a0'},{'q':context.q,'a':'unknown'},
              {'q':context.q,'a':'a0','x':1},{'q':context.q,'a':None}):
        with pytest.raises(ValueError):
            ActionPrefix(context).feed(json.dumps({'d':d})[:-1]+',')
    for text in ('{"d":{"q":"x","q":"y","a":"a0"},',
                 '{"d":{"q":"'+context.q+'","a":"a0"}}'):
        with pytest.raises(ValueError):
            ActionPrefix(context).feed(text)
    with pytest.raises(ValueError,match='duplicate'):
        strict_json('{"d":{},"d":{}}')


def test_short_prefix_binds_request_locally_and_rejects_ambiguous_action(state):
    context=build_context(state,legal_actions(state))
    text='{"a":"a0",'
    for split in range(len(text)):
        prefix=ActionPrefix(context,'short')
        assert prefix.feed(text[:split]) is None
        assert prefix.feed(text) is context.actions['a0']
        assert prefix.committed=={'q':context.q,'a':'a0'}
    for value in ('unknown',{'a':'a0'},None,True):
        with pytest.raises(ValueError):
            ActionPrefix(context,'short').feed('{"a":'+json.dumps(value)+',')


class GatedStream(httpx.AsyncByteStream):
    def __init__(self,body,release,*,broken=False,changed=False,plan=None):
        self.q=json.loads(body['input'][0]['content'])['q']
        self.release,self.broken,self.changed,self.plan=release,broken,changed,plan
        self.short=list(body['text']['format']['schema']['properties'])[0]=='a'

    async def __aiter__(self):
        first={'a':'a0'} if self.short else {'d':{'q':self.q,'a':'a0'}}
        prefix=json.dumps(first,separators=(',',':'))[:-1]+','
        for char in prefix:
            yield sse({'type':'response.output_text.delta','delta':char})
        await self.release.wait()
        if self.broken:
            return
        tail='"r":"保留进张兼顾防守","p":'+json.dumps(self.plan,ensure_ascii=False)+'}'
        if self.changed:
            tail=('"a":"a1",' if self.short else '"d":{"q":"'+self.q+'","a":"a1"},')+tail
        yield sse({'type':'response.output_text.delta','delta':tail})
        yield sse({'type':'response.completed','response':{'status':'completed',
                   'usage':{'input_tokens':123,'output_tokens':45,'total_tokens':168}}})


def client_for(store,release,format='nested',**kwargs):
    seen=[]
    def handler(request):
        # The enclosing asyncio scope owns the action deadline. The socket
        # idle timeout must not retain that shorter limit after a commit.
        assert request.extensions['timeout']['read'] == 2
        body=json.loads(request.content)
        seen.append(body)
        return httpx.Response(200,stream=GatedStream(body,release,**kwargs))
    return ModelClient(Settings(stream_actions=True,stream_action_format=format,json_schema=True,request_timeout=2),store,
                       httpx.MockTransport(handler)),seen


@pytest.mark.parametrize('format',['nested','short'])
async def test_action_ready_before_tail_and_tail_outlives_action_deadline(store,state,format):
    release=asyncio.Event()
    model,seen=client_for(store,release,format)
    context=build_context(state,legal_actions(state))
    choice=await model.begin_decision(context,time.monotonic()+.05)
    assert choice.action is context.actions['a0'] and not choice.completion.done()
    await asyncio.sleep(.08)
    assert not choice.completion.done()
    release.set()
    decision,action,metrics=await choice.completion
    assert action is choice.action and decision.reason=='保留进张兼顾防守'
    assert metrics['usage']['total_tokens']==168 and choice.metrics['usage'] is None
    assert list(seen[0]['text']['format']['schema']['properties'])==(['a','r','p'] if format=='short' else ['d','r','p'])
    assert choice.metrics['action_ready_ms'] <= metrics['elapsed_ms']
    assert seen[0]['reasoning']=={'effort':'none'}
    await model.close()


@pytest.mark.parametrize('bad_tail',['broken','changed'])
@pytest.mark.parametrize('format',['nested','short'])
async def test_committed_action_is_not_replaced_when_tail_fails(store,state,bad_tail,format):
    release=asyncio.Event()
    model,_=client_for(store,release,format,**{bad_tail:True})
    choice=await model.begin_decision(build_context(state,legal_actions(state)),time.monotonic()+1)
    release.set()
    with pytest.raises((ValueError,RuntimeError)):
        await choice.completion
    assert choice.action.kind=='discard'
    assert len(store.records('token_usage'))==1
    if bad_tail=='broken':
        assert store.records('token_usage')[0]['payload']['usage'] is None
    await model.close()


@pytest.mark.parametrize('late',['same','new_plan','new_round'])
async def test_actual_confirmation_does_not_cancel_annotation_or_duplicate_decision(store,state,late):
    store.save_snapshot(state)
    release,pressed=asyncio.Event(),asyncio.Event()
    plan={'v':0,'e':['e1'],'g':'保位','a':'门清速攻','y':[],'p':['效率'],'s':[],'r':'先手'}
    model,_=client_for(store,release,plan=plan)
    engine=SimpleNamespace(store=store,state=state)
    class Executor:
        mouse=SimpleNamespace(stop=threading.Event())
        lease=SimpleNamespace(owner=None)
        async def execute(self,s,action,deadline):
            pressed.set()
            return time.monotonic()
    coordinator=Coordinator(Settings(stream_actions=True,api_key=SecretStr('fixture')),engine,model,Executor())
    await coordinator.start(shadow=False)
    await asyncio.wait_for(pressed.wait(),1)
    await coordinator.task
    decision=store.decisions(state.match_id)[0]
    assert decision['payload']['annotation_status']=='pending'
    action=coordinator.pending['receipt']['action']
    discard=event('ActionDiscardTile',{'seat':0,'tile':state.hand[action['tile_index']]},2)
    engine.state=apply_event(state,discard)
    await coordinator.changed(engine.state,discard)
    assert store.records('receipt')[0]['payload']['status']=='server_confirmed'
    if late=='new_plan':
        coordinator.plan={'version':1,'goal':'较新思路'}
    elif late=='new_round':
        coordinator.round_uid='next-round'
    release.set()
    await asyncio.gather(*coordinator.annotation_tasks)
    assert len(store.records('decision'))==1
    merged=store.decisions(state.match_id)[0]['payload']
    assert merged['annotation_status']=='complete' and merged['decision']['reason']=='保留进张兼顾防守'
    assert merged['metrics']['usage']['total_tokens']==168
    assert not store.records('decision_failure')
    if late=='same':
        assert coordinator.plan['version']==1 and coordinator.plan['goal']=='保位'
    else:
        assert store.records('round_plan_late_ignored')
        if late=='new_plan':
            assert coordinator.plan['goal']=='较新思路'
        else:
            assert coordinator.plan is None
    await coordinator.stop()
    await model.close()


@pytest.mark.parametrize('format',['nested','short'])
async def test_prefix_error_repairs_before_commit_and_never_selects_a_local_fallback(store,state,format):
    context=build_context(state,legal_actions(state))
    calls=[]
    release=asyncio.Event()
    release.set()
    def handler(request):
        body=json.loads(request.content)
        calls.append(body)
        if len(calls)==1:
            prefix = '{"a":"unknown",' if format == 'short' else '{"d":{"q":"wrong","a":"a0"},'
            return httpx.Response(200,text='data: '+json.dumps({'type':'response.output_text.delta',
                'delta':prefix})+'\n\n')
        return httpx.Response(200,stream=GatedStream(body,release))
    model=ModelClient(Settings(json_schema=True,stream_action_format=format),store,httpx.MockTransport(handler))
    choice=await model.begin_decision(context,time.monotonic()+2)
    await choice.completion
    assert len(calls)==2 and 'repair_instruction' in json.loads(calls[1]['input'][0]['content'])
    repair=json.loads(calls[1]['input'][0]['content'])['repair_instruction']
    assert ('合法a字段' if format == 'short' else '合法d对象') in repair
    assert len(store.records('token_usage'))==2 and not store.records('decision')
    await model.close()


async def test_explicit_stop_cancels_pending_annotation_without_extra_click(store,state):
    release=asyncio.Event()
    model,_=client_for(store,release)
    engine=SimpleNamespace(store=store,state=state)
    coordinator=Coordinator(Settings(stream_actions=True,api_key=SecretStr('fixture')),engine,model)
    await coordinator.start(shadow=True)
    await coordinator.task
    assert coordinator.annotation_tasks
    await coordinator.stop()
    assert store.records('decision_annotation')[0]['payload']['status']=='cancelled'
    assert not store.records('receipt')
    release.set()
    await model.close()
