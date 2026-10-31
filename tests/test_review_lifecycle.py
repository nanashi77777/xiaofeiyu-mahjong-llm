import asyncio
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


async def test_review_attempt_cap_survives_restart_and_preemption(store, state):
    state, _ = add_match(store,state,0)
    store.record('review_job',{'status':'running','attempt':2,'source_key':state.last_event_id},state.match_id)
    entered = asyncio.Event()
    async def request(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    reviewer = Reviewer(store,SimpleNamespace(request=request),lambda:False)
    await reviewer.resume_pending()
    await asyncio.wait_for(entered.wait(),1)
    assert reviewer.attempts[state.match_id] == 3
    await reviewer.pause_for_decision()
    await asyncio.wait_for(reviewer.queue.join(),1)
    assert store.records('review_job',state.match_id,1)[0]['payload']['status'] == 'failed'
    reviewer.worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reviewer.worker
    restarted = Reviewer(store,SimpleNamespace(request=request),lambda:False)
    try:
        await restarted.resume_pending()
        await restarted.enqueue(state.match_id)
        assert restarted.queue.empty() and not restarted.queued
        state.last_event_id = 'new-recorded-source'
        store.save_snapshot(state)
        await restarted.enqueue(state.match_id)
        assert restarted.attempts[state.match_id] == 0
    finally:
        restarted.worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await restarted.worker


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


def test_context_recall_freezes_versions_and_bounded_style_without_changing_actions(store, state):
    source, evidence = add_match(store,state,0)
    memory = Memories(store)
    scoped = memory.revise({'claim':'第三位面对立直重新衡量保位', 'supports':[evidence],
        'applies_to':{'ranks':[3], 'opponent_riichi':True, 'phases':['awaiting_discard']}},source.match_id)
    style = memory.revise({'kind':'style','claim':'接近终场时侧重保位但仍比较进攻收益',
        'preference':{'axis':'placement_defense','value':1},'supports':[evidence]},source.match_id)
    for _ in range(10):
        memory.revise({'claim':'仅适用于副露局', 'supports':[evidence],
                       'applies_to':{'menzen':False}},source.match_id)
    frozen = memory.freeze()
    state.scores = [20000,30000,25000,15000]
    state.players[1].riichi = 'established'
    recalled = memory.recall(frozen,state)
    assert {m['id'] for m in recalled} == {scoped['id'],style['id']}
    assert style['preference']['value'] == 1 and style['status'] == 'candidate'
    projection = memory.projection(recalled)
    assert all('supports' not in m and 'support_count' in m for m in projection)
    memory.revise({'claim':'本场开始后新增，下一场才有资格召回','supports':[evidence]},source.match_id)
    assert {m['id'] for m in memory.recall(frozen,state)} == {scoped['id'],style['id']}
    memory.revise({'id':scoped['id'],'revision':1,'status':'inactive','reason':'发现反例'},source.match_id)
    assert [m['id'] for m in memory.recall(frozen,state)] == [style['id']]
    with pytest.raises(ValueError):
        memory.revise({'kind':'style','claim':'越界','supports':[evidence],
                       'preference':{'axis':'placement_defense','value':3}},source.match_id)
    changed = memory.revise({'id':style['id'],'revision':1,'kind':'lesson','claim':'改成具体经验',
                             'supports':[evidence]},source.match_id)
    assert changed['preference'] is None
    assert memory.recall(frozen,state) == []
