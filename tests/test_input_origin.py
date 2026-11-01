import asyncio
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import event
from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.engine import Engine
from majsoul_llm.input_origin import input_origin
from majsoul_llm.rules import legal_actions
from majsoul_llm.state import apply_event
from majsoul_llm.store import utcnow
from majsoul_llm.validation import match_report


@pytest.mark.parametrize('payload,expected', [
    ({'timeuse':1000000}, 'client_automatic'),
    ({'timeuse':5}, 'unverified'), ({}, 'unverified'),
    ({'timeuse':True}, 'unverified'), ({'timeuse':1000000.0}, 'unverified'),
    ({'timeuse':'1000000'}, 'unverified'), ({'timeuse':1000001}, 'unverified'),
])
def test_origin_requires_exact_verified_marker_and_never_infers_bot(payload,expected):
    assert input_origin(payload) == expected


def pending_coordinator(store,state,kind='pass',status='mouse_sent'):
    c = Coordinator(Settings(),SimpleNamespace(store=store,state=state),None)
    action = legal_actions(state)[0].model_copy(update={'kind':kind})
    c.enabled = True
    c.pending = {'receipt':{'id':'input-receipt','action':action.model_dump(),
        'status':status,'created_at':utcnow()},'state':state,'start':time.monotonic(),
        'proposed_plan':{'version':1,'goal':'must not adopt an automatic action'}}
    c.round_uid = state.round_uid
    c.bank.enter_round(state.round_uid)
    return c, action


@pytest.mark.parametrize('kind',['pass','discard','pon'])
async def test_automatic_accepted_input_is_never_bot_confirmation(store,state,kind):
    c,action = pending_coordinator(store,state,kind)
    payload = {'cancel_operation':True,'timeuse':1000000}
    await c.input_observed('request',action.window_id,payload,'request-record')
    await c.input_observed('accepted',action.window_id,payload,'ack-record')
    receipt = store.records('receipt')[0]['payload']
    assert receipt['status'] == 'mismatch'
    assert receipt['reason'] == 'client_automatic_input_not_bot_confirmation'
    assert receipt['client_input_ids'] == ['request-record'] and receipt['actual_ack_id'] == 'ack-record'
    assert c.pending is None and not c.enabled and not store.records('round_plan')
    assert c.engine.state is state


@pytest.mark.parametrize('automatic',[False,True])
async def test_early_ack_waits_for_mouse_transaction_and_retains_request_reference(store,state,automatic):
    c,action = pending_coordinator(store,state,status='validated')
    payload = {'cancel_operation':True,'timeuse':1000000 if automatic else 3}
    await c.input_observed('request',action.window_id,payload,'request-record')
    await c.input_observed('request',action.window_id,payload,'request-record')
    await c.input_observed('accepted',action.window_id,payload,'ack-record')
    assert not store.records('receipt') and c.pending['early_ack'] == (payload,'ack-record')
    assert c.pending['receipt']['client_input_ids'] == ['request-record']
    c.pending['receipt']['status'] = 'mouse_sent'
    early_payload, ack = c.pending.pop('early_ack')
    await c.input_observed('accepted',action.window_id,early_payload,ack)
    receipt = store.records('receipt')[0]['payload']
    assert receipt['status'] == ('mismatch' if automatic else 'server_confirmed')
    assert bool(store.records('round_plan')) is not automatic
    assert c.pending is None


async def test_same_tile_automatic_event_preserves_facts_without_confirming_model(store,state):
    c,action = pending_coordinator(store,state,'discard')
    payload = {'type':1,'tile':state.hand[action.tile_index],'timeuse':1000000}
    await c.input_observed('request',action.window_id,payload,'request-record')
    actual = event('ActionDiscardTile',{'seat':0,'tile':payload['tile']},2)
    real_state = apply_event(state,actual)
    c.confirm(real_state,actual)
    receipt = store.records('receipt')[0]['payload']
    assert receipt['status'] == 'mismatch' and receipt['actual_event_id'] == actual.id
    assert receipt['reason'] == 'client_automatic_input_not_bot_confirmation'
    assert real_state.hand == state.hand[1:]
    assert real_state.players[0].river[-1].tile == payload['tile']
    assert not c.enabled and not store.records('round_plan')


async def test_other_window_no_pending_and_rejected_ack_cannot_confirm(store,state):
    c,action = pending_coordinator(store,state)
    payload = {'cancel_operation':True,'timeuse':1000000}
    await c.input_observed('request','another-window',payload,'other-request')
    await c.input_observed('accepted','another-window',payload,'other-ack')
    assert 'automatic_input' not in c.pending and not store.records('receipt')
    await c.input_observed('rejected',action.window_id,payload,'rejected-ack')
    assert not c.enabled and not store.records('receipt')
    await c.stop('human_stop')
    count = len(store.records('receipt'))
    await c.input_observed('accepted',action.window_id,payload,'late-ack')
    assert len(store.records('receipt')) == count and c.pending is None
    assert store.records('receipt')[0]['payload']['status'] == 'cancelled'


async def test_automatic_input_during_model_wait_cancels_without_retry_or_click(store,state):
    c = Coordinator(Settings(),SimpleNamespace(store=store,state=state),None)
    c.task = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)
    await c.input_observed('request',state.operation.window_id,{'timeuse':1000000},'request-record')
    result = await asyncio.gather(c.task,return_exceptions=True)
    assert isinstance(result[0],asyncio.CancelledError)
    assert not store.records('decision') and not store.records('receipt')


@pytest.mark.parametrize('automatic',[False,True])
async def test_engine_links_request_and_ack_and_keeps_actual_pass_furiten(store,state,automatic):
    state.hand.pop()
    state.operation.raw['operation_list'] = [{'type':9}]
    store.save_snapshot(state)
    engine = Engine(store)
    engine.state,engine.connection = state,'ours'
    calls = []
    async def on_input(*args):
        calls.append(args)
    engine.on_input = on_input
    data = {'cancel_operation':True,'timeuse':1000000 if automatic else 4}
    request = {'method':'.lq.FastTest.inputChiPengGang','direction':'request','connection':'ours',
               'request_id':5,'data':data}
    await engine.message({**request,'connection':'other'})
    assert not store.records('client_input')
    await engine.message(request)
    request_row = store.records('client_input')[0]
    assert calls[0][3] == request_row['id']
    assert request_row['payload']['method'] == 'inputChiPengGang'
    assert request_row['payload']['input_origin'] == ('client_automatic' if automatic else 'unverified')
    response = {**request,'direction':'response','data':{},'original_input':data}
    await engine.message(response)
    ack = store.records('server_input_ack')[0]
    assert ack['payload']['request_record_id'] == request_row['id'] and ack['payload']['accepted']
    assert calls[1][3] == ack['id']
    assert not engine.input_records and not engine.input_windows
    assert engine.state.operation is None and engine.state.furiten['temporary']
    assert store.events(state.match_id)[-1]['kind'] == 'ObservedPass'


def test_report_excludes_old_false_success_without_modifying_original_rows():
    events = [{'id':'event','received_at':'test','data':{'operation':{
        'seat':0,'operation_list':[{'type':1}],'time_fixed':5000,'time_add':10000}}}]
    action = {'kind':'discard','window_id':'event:op'}
    rows = {
        'decision':[{'id':'decision','purpose':'decision','created_at':'a','payload':{'action':action}}],
        'receipt':[
            {'id':'sent','purpose':'receipt','created_at':'b','payload':{'action':action,'status':'mouse_sent','read_to_mouse_ms':4000}},
            {'id':'confirmed','purpose':'receipt','created_at':'c','payload':{'action':action,'status':'server_confirmed'}}],
        'client_input':[{'id':'request','payload':{'window_id':'event:op','timeuse':1000000}},
                        {'id':'unscoped','payload':{'timeuse':1000000}}],
        'server_input_ack':[{'id':'ack','payload':{'window_id':'event:op','accepted':True}}],
    }
    before = json.dumps(rows,sort_keys=True)
    fake = SimpleNamespace(latest=lambda mid:SimpleNamespace(self_seat=0,lifecycle='active',completeness='full',
        consistent=True,round_seq=1,final_scores=None,final_ranks=None),
        events=lambda mid,offset,limit:events[offset:offset+limit],
        records=lambda purpose,mid,limit:rows.get(purpose,[]))
    result = match_report(fake,'synthetic')
    assert result['opportunities'] == 1 and result['server_confirmed'] == 0
    assert result['within_5s_confirmed'] == 0 and result['within_5s_rate'] == 0
    assert result['windows']['event:op']['recorded_server_confirmed']
    assert result['windows']['event:op']['input_ack_refs'][0]['request_id'] is None
    assert len(result['client_automatic_inputs']) == 2
    assert json.dumps(rows,sort_keys=True) == before


@pytest.mark.parametrize('failure',[None,'wrong_scope','automatic_confirmation'])
def test_read_only_audit_checks_references_and_never_rewrites_old_records(store,state,failure):
    path = Path(__file__).parents[1] / 'tools/audit_input_attribution.py'
    spec = importlib.util.spec_from_file_location('input_audit',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = {'method':'inputOperation','window_id':state.operation.window_id,'timeuse':3}
    if failure == 'automatic_confirmation':
        request['timeuse'] = 1000000
    ref = store.record('client_input',request,state.match_id,state.round_uid)
    ack = store.record('server_input_ack',{'method':'inputOperation',
        'window_id':state.operation.window_id,'request_record_id':ref,'accepted':True,'input':request},
        'other-match' if failure == 'wrong_scope' else state.match_id,state.round_uid)
    action = legal_actions(state)[0]
    store.record('receipt',{'action':action.model_dump(),'status':'server_confirmed',
        'client_input_ids':[ref],'actual_ack_id':ack},state.match_id,state.round_uid)
    store.record('server_input_ack',{'window_id':'legacy','accepted':True},state.match_id,state.round_uid)
    before = store.db.execute('SELECT count(*) FROM records').fetchone()[0]
    database = Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    result = module.audit(database)
    assert result['passed'] is (failure is None)
    assert result['reference_counts']['legacy_unlinked_acks'] == 1
    assert not result['production_data_modified'] and result['model_requests'] == 0
    assert before == store.db.execute('SELECT count(*) FROM records').fetchone()[0]
    if failure:
        assert result['errors']
