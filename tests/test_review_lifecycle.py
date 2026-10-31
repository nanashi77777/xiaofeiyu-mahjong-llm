from types import SimpleNamespace

import pytest

from majsoul_llm.domain import Event
from majsoul_llm.memory import Memories
from majsoul_llm.review import Reviewer


def add_match(store, state, index):
    state = state.model_copy(deep=True, update={'match_id':f'source-{index}', 'lifecycle':'ended',
                                               'round_uid':f'source-{index}:round'})
    stamp = f'2026-01-{index+1:02d}T00:00:00+00:00'
    state.observed_at = stamp
    event = Event(id=f'source-{index}-e1', match_id=state.match_id, seq=1, kind='ActionNewRound',
                  data={}, received_at=stamp, received_mono=0, source='fixture')
    store.save_event(event, state)
    return state, event.id


async def test_review_can_request_specific_yaku_and_records_version(store, state):
    state, source = add_match(store,state,0)
    calls = []
    async def request(payload, **kwargs):
        calls.append({**payload})
        if len(calls) == 1:
            return {'read_yaku_reference':{'ids':['tanyao'],'topic':'','all_entries':False}}, {}
        return {'summary':'核对副露断幺条件', 'lessons':[{'claim':'含幺九副露不能达成断幺',
                    'supports':[source], 'counterexamples':[], 'yaku_ids':['tanyao']}]}, {}
    reviewer = Reviewer(store,SimpleNamespace(request=request),lambda:False)
    await reviewer.review(state.match_id)
    assert not calls[0]['yaku_reference']['entries']
    lookup = calls[1]['reference_lookups'][0]['result']
    assert [e['id'] for e in lookup['entries']] == ['tanyao']
    assert store.records('review_reference_lookup')
    m = Memories(store).query()[0]
    assert m['catalog_version'] == lookup['catalog_version'] and m['status'] == 'candidate'


async def test_review_batch_rolls_back_when_later_evidence_is_invalid(store, state):
    state, source = add_match(store,state,0)
    async def request(payload, **kwargs):
        return {'summary':'测试事务', 'lessons':[{'claim':'有效证据', 'supports':[source]},
                                             {'claim':'错误引用', 'supports':['nonexistent']}]}, {}
    reviewer = Reviewer(store,SimpleNamespace(request=request),lambda:False)
    with pytest.raises(ValueError,match='support'):
        await reviewer.review(state.match_id)
    assert Memories(store).query() == [] and store.records('review') == []


def test_memory_needs_evidence_in_three_subsequent_matches_and_resets_changed_claim(store, state):
    m = Memories(store)
    origin, evidence = add_match(store,state,0)
    memory = m.revise({'claim':'低价值手受立直威胁时重新评估追打', 'supports':[evidence]},origin.match_id)
    for i in range(1,4):
        next_state, evidence = add_match(store,state,i)
        # Merely claiming that an arbitrary match was reviewed never counts.
        same = m.revise({'id':memory['id'],'revision':memory['revision'],
                         'reviewed_matches':['invented-match']},next_state.match_id)
        assert len(same['reviewed_matches']) == i
        memory = m.revise({'id':same['id'],'revision':same['revision'],
                           'supports':[evidence],'counterexamples':[],
                           'status':'stable' if i == 3 else 'candidate'},next_state.match_id)
    assert memory['status'] == 'stable' and len(memory['reviewed_matches']) == 4
    changed = m.revise({'id':memory['id'], 'revision':memory['revision'], 'claim':'新的具体适用主张',
                        'status':'candidate', 'supports':[evidence]},next_state.match_id)
    assert changed['reviewed_matches'] == [next_state.match_id]
    assert changed['origin_match'] == next_state.match_id
    assert len(m.history(memory['id'])) == 8
