import json

import pytest

from conftest import event
from majsoul_llm.rules import legal_actions
from majsoul_llm.state import apply_event
from majsoul_llm.wire import build_context, pack_rows, semantic_context, unpack_rows


def test_named_rows_preserve_absent_null_order_and_duplicates():
    rows = [{'tile':'0m','called_by':None}, {'tile':'5m'}, {'tile':'0m','called_by':None}, {}]
    table = pack_rows(rows)
    assert len(table['columns']) == 3
    assert unpack_rows(table) == rows
    assert unpack_rows(pack_rows([])) == []
    for broken in ({'columns':[['x','x']],'rows':[]}, {'columns':[['x']],'rows':[[0]]},
                   {'columns':[['x']],'rows':[[True,1]]}, {'columns':[],'rows':[[0]]}):
        with pytest.raises(ValueError):
            unpack_rows(broken)


def test_table_context_preserves_complete_rivers_facts_and_plan_reference(state):
    operation = state.operation.model_copy(deep=True)
    for i in range(1,25):
        state = apply_event(state,event('ActionDealTile',{'seat':1},i*2))
        state = apply_event(state,event('ActionDiscardTile',{'seat':1,'tile':'0p' if i==3 else '5p',
                                                        'moqie':i%2==0},i*2+1))
    # The test deliberately contains 24 entries, above a normal visible row;
    # transport never truncates the authoritative sequence.
    state.unknown_fields = ['fixture_unknown']
    state.context['explicit_unknown'] = None
    # Opponent events close the old window; restore only this constructed offer.
    state.operation = operation.model_copy(update={'window_id':'table-fixture','state_version':state.state_version})
    actions = legal_actions(state)
    rich = build_context(state,actions)
    readable = build_context(state,actions,compact=True)
    tabled = build_context(state,actions,compact=True,tabled=True)
    assert semantic_context(rich) == semantic_context(readable) == semantic_context(tabled)
    assert len(unpack_rows(tabled.data['players'][1]['river'])) == 24
    assert len(tabled.events) == len(state.public_events)
    assert len(json.dumps(tabled.data)) < len(json.dumps(readable.data))
    ids = [e['event_id'] for e in tabled.events]
    assert tabled.response_schema()['$defs']['RoundPlanUpdate']['properties']['e']['items']['enum'] == ids
    raw = {'q':tabled.q,'a':'a0','r':'核对全部牌河后决策','p':{'v':0,'e':[ids[-1]],'g':'保位',
           'a':'门清推进','y':[],'p':['效率'],'s':[],'r':'构造验证'}}
    decision, _ = tabled.validate(raw)
    assert decision.plan_update.basis_event_ids == [state.public_events[-1]['event_id']]
    raw['p']['e'] = ['unknown-event']
    with pytest.raises(ValueError,match='unknown evidence'):
        tabled.validate(raw)
    with pytest.raises(ValueError,match='validated readable'):
        build_context(state,actions,tabled=True)
