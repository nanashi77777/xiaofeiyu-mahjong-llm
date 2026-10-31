import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from majsoul_llm.domain import ExperienceRevision
from majsoul_llm.memory import Memories
from majsoul_llm.review import MemoryProposal, Reviewer
from majsoul_llm.store import packed
from test_review_lifecycle import add_match


def test_recall_eight_respects_scope_and_never_exceeds_hard_limit(store, state):
    source, event = add_match(store, state, 0)
    memories = Memories(store)
    eligible = []
    for i in range(10):
        eligible.append(memories.revise({'claim':f'第{i}条：低价值手受威胁时比较进攻收益与失点风险',
            'applies_to':{'menzen':True}, 'supports':[event]}, source.match_id)['id'])
    for i in range(3):
        memories.revise({'claim':f'仅适用于副露的独立经验{i}', 'applies_to':{'menzen':False},
                         'supports':[event]}, source.match_id)
    frozen = memories.freeze()
    recalled = memories.recall(frozen, state)
    assert len(recalled) == 8 and {m['id'] for m in recalled} <= set(eligible)
    assert len(memories.recall(frozen, state, limit=100)) == 8
    assert len(memories.recall(frozen, state, limit=2)) == 2
    assert memories.recall(frozen, state, limit=0) == []
    for invalid in (-1, 1.5, True):
        with pytest.raises(ValueError):
            memories.recall(frozen, state, limit=invalid)
    before = {m['id'] for m in recalled}
    memories.revise({'claim':'开场后新增，下场才用', 'supports':[event]}, source.match_id)
    assert {m['id'] for m in memories.recall(frozen, state)} == before


@pytest.mark.parametrize('text', [
    {'claim':'长'*121}, {'claim':'短经验', 'conditions':['限'*65]},
    {'claim':'短经验', 'conditions':['甲','乙','丙','丁']}, {'claim':'短经验', 'reason':'因'*161}])
async def test_generation_and_storage_reject_long_text_without_partial_commit(store, state, text):
    source, event = add_match(store, state, 0)
    proposal = {**text, 'supports':[event]}
    with pytest.raises(ValueError):
        Memories(store).revise(proposal, source.match_id)
    assert Memories(store).query() == []
    async def request(payload, **kwargs):
        return {'summary':'只验证生成约束', 'lessons':[
            {'claim':'可以保存的短经验', 'supports':[event]}, proposal]}, {}
    reviewer = Reviewer(store, SimpleNamespace(request=request), lambda:False)
    with pytest.raises(ValidationError):
        await reviewer.review(source.match_id)
    assert Memories(store).query() == [] and not store.records('review')
    raw = store.records('review_proposal')[0]['payload']['output']['lessons'][1]
    assert all(raw[k] == value for k, value in text.items())  # Diagnostic output is never truncated.


async def test_short_generation_retains_exception_evidence_and_shared_limits(store, state):
    source, event = add_match(store, state, 0)
    claim = '低价值手受立直威胁时重估追打，不因一次放铳认定进攻错误'
    conditions = ['已有高收益听牌时仍比较追打收益，不一概弃和', '安全牌不足不等于可忽略现物以外的危险']
    async def request(payload, **kwargs):
        schema = kwargs['output_schema']['$defs']['MemoryProposal']['properties']
        assert next(v for v in schema['claim']['anyOf'] if v.get('type')=='string')['maxLength'] == 120
        condition_schema = next(v for v in schema['conditions']['anyOf'] if v.get('type')=='array')
        assert condition_schema['maxItems'] == 3 and condition_schema['items']['maxLength'] == 64
        assert '不能删去改变结论的限制或例外' in kwargs['system']
        assert payload['input_version'] == 'review-input.v3-concise-experience'
        return {'summary':'保留边界的短经验', 'lessons':[{'claim':claim, 'conditions':conditions,
            'supports':[event], 'counterexamples':[event], 'reason':'复核当时收益与风险'}]}, {}
    reviewer = Reviewer(store, SimpleNamespace(request=request), lambda:False)
    await reviewer.review(source.match_id)
    result = Memories(store).query()[0]
    assert result['claim'] == claim and result['conditions'] == conditions
    assert result['supports'] == [event] and result['counterexamples'] == [event]
    assert result['status'] == 'candidate'


def test_legacy_long_memory_can_be_read_supported_and_disabled_without_rewrite(store, state):
    source, event = add_match(store, state, 0)
    memories = Memories(store)
    original = memories.revise({'claim':'原始短经验', 'supports':[event]}, source.match_id)
    # A valid archived v1 record written under the old 1200-character policy.
    legacy = {**original, 'claim':'旧经验适用条件与例外'*30, 'conditions':['旧边界'*30]}
    ExperienceRevision.model_validate(legacy)
    with store.db:
        store.db.execute('UPDATE memories SET payload=? WHERE id=? AND revision=1', (packed(legacy), legacy['id']))
    assert memories.query()[0]['claim'] == legacy['claim']
    assert memories.projection(memories.recall(memories.freeze(), state))[0]['conditions'] == legacy['conditions']
    next_source, next_event = add_match(store, state, 1)
    supported = memories.revise({'id':legacy['id'], 'revision':1, 'supports':[next_event],
        'reason':'只补证，不自动改历史措辞'}, next_source.match_id)
    assert supported['claim'] == legacy['claim'] and supported['conditions'] == legacy['conditions']
    assert len(supported['reviewed_matches']) == 2
    inactive = memories.revise({'id':legacy['id'], 'revision':2, 'status':'inactive',
                               'reason':'发现适用边界有误'}, next_source.match_id)
    assert inactive['claim'] == legacy['claim'] and not memories.recall([legacy], state)
    assert json.loads(store.db.execute('SELECT payload FROM memories WHERE id=? AND revision=1',
                                     (legacy['id'],)).fetchone()[0]) == legacy


def test_explicit_short_rewrite_is_candidate_with_new_evidence(store, state):
    source, event = add_match(store, state, 0)
    m = Memories(store)
    original = m.revise({'claim':'原先主张过于笼统', 'supports':[event]}, source.match_id)
    following, evidence = add_match(store, state, 1)
    rewritten = m.revise({'id':original['id'], 'revision':1, 'claim':'低打点受立直威胁时比较追打收益',
        'conditions':['不能以一次输赢验证原则'], 'supports':[evidence]}, following.match_id)
    assert rewritten['status']=='candidate' and rewritten['supports']==[evidence]
    assert rewritten['reviewed_matches']==[following.match_id] and rewritten['origin_match']==following.match_id


def test_empty_conditions_and_omitted_unchanged_text_are_valid():
    proposal = MemoryProposal(id='existing', revision=1, status='inactive', reason='错误经验停用')
    assert 'claim' not in proposal.model_dump(exclude_none=True)
    assert 'conditions' not in proposal.model_dump(exclude_none=True)
    assert MemoryProposal(claim='独立短原则', conditions=[]).conditions == []
