"""Request races choose the first legal model prefix, with one shared deadline."""
import asyncio
import json
import time

import httpx
import pytest

from majsoul_llm.config import Settings
from majsoul_llm.model import ModelClient, ModelFailure
from majsoul_llm.rules import legal_actions
from majsoul_llm.wire import build_context


def sse(value):
    return ('data: '+json.dumps(value)+'\n\n').encode()


class DelayedStream(httpx.AsyncByteStream):
    def __init__(self, delay, action, tail, *, broken=False):
        self.delay,self.action,self.tail,self.broken=delay,action,tail,broken

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        yield sse({'type':'response.output_text.delta','delta':'{"a":"'+self.action+'",'})
        await self.tail.wait()
        if self.broken:
            return
        yield sse({'type':'response.output_text.delta','delta':'"r":"保留进张兼顾防守","p":null}'})
        yield sse({'type':'response.completed','response':{'status':'completed',
                   'usage':{'input_tokens':10,'output_tokens':5,'total_tokens':15}}})


def race_client(store, timings, actions, *, tail=None, broken=False):
    bodies=[]
    tail=tail or asyncio.Event()
    def handler(request):
        index=len(bodies)
        body=json.loads(request.content)
        bodies.append(body)
        assert index<2
        assert body['reasoning']=={'effort':'none'}
        assert 'race_id' not in body['input'][0]['content']
        return httpx.Response(200,stream=DelayedStream(timings[index],actions[index],tail,broken=broken))
    settings=Settings(stream_hedge_delay_seconds=.03,stream_action_format='short',json_schema=True,request_timeout=2)
    return ModelClient(settings,store,httpx.MockTransport(handler)),bodies,tail


@pytest.mark.parametrize('timings,winner,requests', [([0,0],0,1),([.3,0],1,2),([.07,.3],0,2),([.05,.02],0,2)])
async def test_race_has_single_winner_same_payload_and_no_unneeded_request(store,state,timings,winner,requests):
    model,bodies,tail=race_client(store,timings,['a0','a1'])
    context=build_context(state,legal_actions(state))
    choice=await model.begin_decision(context,time.monotonic()+1)
    assert choice.action is context.actions['a'+str(winner)]
    assert choice.metrics['race_lane']==winner and len(bodies)==requests
    if requests==2:
        assert bodies[0]==bodies[1]
    assert not choice.completion.done()
    tail.set()
    decision,action,metrics=await choice.completion
    assert action is choice.action and decision.reason=='保留进张兼顾防守'
    usage=store.records('token_usage')
    assert len(usage)==requests
    assert len({r['payload']['request_id'] for r in usage})==requests
    assert len({r['payload']['race_id'] for r in usage})==1
    assert sum(r['payload']['usage'] is None for r in usage)==requests-1
    assert len(store.records('stream_race'))==1
    await model.close()


async def test_invalid_primary_starts_only_one_independent_second_attempt(store,state):
    model,bodies,tail=race_client(store,[0,0],['unknown','a1'])
    tail.set()
    context=build_context(state,legal_actions(state))
    choice=await model.begin_decision(context,time.monotonic()+1)
    assert choice.action is context.actions['a1'] and len(bodies)==2
    await choice.completion
    assert len(store.records('token_usage'))==2
    await model.close()


@pytest.mark.parametrize('stop',['deadline','cancel','close','both_invalid'])
async def test_failure_or_stop_never_leaves_requests_or_picks_local_action(store,state,stop):
    timings=[0,0] if stop=='both_invalid' else [1,1]
    actions=['unknown','unknown'] if stop=='both_invalid' else ['a0','a1']
    model,bodies,_=race_client(store,timings,actions)
    context=build_context(state,legal_actions(state))
    task=asyncio.create_task(model.begin_decision(context,time.monotonic()+(.07 if stop=='deadline' else 2)))
    if stop in ('cancel','close'):
        await asyncio.sleep(.05)
        if stop=='cancel':
            task.cancel()
        else:
            await model.close()
    with pytest.raises((ModelFailure,asyncio.CancelledError)):
        await task
    await model.close()
    assert len(bodies)==2 and len(store.records('token_usage'))==2
    assert not model.streams and not model.races and not store.records('stream_race')


async def test_winning_prefix_not_replaced_by_loser_when_winner_tail_fails(store,state):
    model,bodies,tail=race_client(store,[.3,0],['a0','a1'],broken=True)
    context=build_context(state,legal_actions(state))
    choice=await model.begin_decision(context,time.monotonic()+1)
    tail.set()
    with pytest.raises(ModelFailure):
        await choice.completion
    assert choice.action is context.actions['a1'] and len(bodies)==2
    await model.close()
