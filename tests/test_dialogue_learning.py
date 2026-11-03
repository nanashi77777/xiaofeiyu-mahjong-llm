import json
import time
from types import SimpleNamespace

import pytest

from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.knowledge import Knowledge, KnowledgeProposal, learning_projection, recall_learning
from majsoul_llm.memory import Memories
from majsoul_llm.operations import OperationRequest
from majsoul_llm.review import Reviewer
from majsoul_llm.rules import legal_actions
from test_review_lifecycle import add_match


def teach(store,**changes):
    return Knowledge(store).write({'claim':'领先低价值手面对立直时重新比较追打和防守收益',
        'conditions':['高价值好型听牌仍需比较进攻收益'],**changes},
        source_quote='领先低价值手面对立直时重新比较追打和防守收益',source_ref='synthetic-admin-input')


def test_teaching_without_fake_match_revision_dedup_and_public_provenance(store):
    k=Knowledge(store)
    first=teach(store)
    assert first['trust']=='taught_unverified' and first['revision']==1
    assert 'origin_match' not in first and 'source_digest' not in first
    assert teach(store)['deduplicated'] and len(k.query())==1
    with pytest.raises(ValueError,match='conflict'):
        teach(store,id=first['id'],revision=9)
    second=teach(store,id=first['id'],revision=1,conditions=['无需因一次输赢定论'])
    assert second['revision']==2
    disabled=k.write({'id':first['id'],'revision':2,'status':'inactive','reason':'重新学习'},
        source_quote='停用这条麻将技巧',source_ref='new-admin-input')
    assert disabled['status']=='inactive' and not k.query()
    assert len(k.read(ids=[first['id']])['revisions'])==3
    assert not store.recent()


@pytest.mark.parametrize('proposal',[
    {'claim':'长'*121},{'claim':'x','conditions':['长'*65]},
    {'claim':'x','applies_to':{'min_discards':12,'max_discards':2}},
    {'claim':'x','applies_to':{'min_remaining':99,'max_remaining':9}},
    {'claim':'x','kind':'style'},{'id':'tip_'+'a'*32,'claim':'缺版本'},
])
def test_teaching_rejects_invalid_text_scope_and_versions(store,proposal):
    with pytest.raises(ValueError):
        Knowledge(store).write(proposal,source_quote='管理员当前教学',source_ref='synthetic')
    assert not Knowledge(store).query()


def test_old_experience_beyond_fifty_is_recalled_and_freeze_is_small(store,state):
    source,event=add_match(store,state,0)
    m=Memories(store)
    oldest=m.revise({'claim':'门清时考虑保留有役路线','applies_to':{'menzen':True},'supports':[event]},source.match_id)
    for i in range(65):
        m.revise({'claim':f'副露专用样本{i}','applies_to':{'menzen':False},'supports':[event]},source.match_id)
    frozen=m.freeze()
    assert len(frozen)==66 and all(set(ref)=={'id','revision'} for ref in frozen)
    assert [r['id'] for r in recall_learning(store,frozen,state)]==[oldest['id']]
    assert len(json.dumps(frozen))<len(json.dumps(m.query(limit=None)))//3


def test_shared_budget_conditions_next_decision_revision_and_immediate_disable(store,state):
    source,event=add_match(store,state,0)
    m=Memories(store)
    experience=m.revise({'claim':'通用防守原则','supports':[event]},source.match_id)
    frozen=m.freeze()
    before=recall_learning(store,frozen,state)
    first=teach(store,applies_to={'opponent_riichi':True,'min_score_gap':5000,'max_remaining':30})
    assert {r['id'] for r in recall_learning(store,frozen,state)}=={experience['id']}
    state.scores=[40000,20000,20000,20000]
    state.players[1].riichi='established'
    state.remaining=20
    selected=recall_learning(store,frozen,state)
    assert selected[0]['id']==first['id'] and len(before)==1
    snapshot=learning_projection(selected)
    Knowledge(store).write({'id':first['id'],'revision':1,'claim':'领先低价值手先衡量安全退路'},
        source_quote='领先低价值手先衡量安全退路',source_ref='admin-update')
    assert snapshot[0]['claim']==first['claim']
    assert recall_learning(store,frozen,state)[0]['revision']==2
    Knowledge(store).write({'id':first['id'],'revision':2,'status':'inactive'},
        source_quote='停用这条',source_ref='admin-disable')
    assert {r['id'] for r in recall_learning(store,frozen,state)}=={experience['id']}
    m.disable(experience['id'],1,'管理员指出适用边界错误')
    assert not recall_learning(store,frozen,state)
    assert m.history(experience['id'])[1]['status']=='candidate'
    for i in range(12):
        teach(store,claim=f'第{i}条独立具体技巧')
    assert len(recall_learning(store,frozen,state))==8
    assert len(recall_learning(store,frozen,state,limit=2))==2
    assert recall_learning(store,frozen,state,limit=0)==[]


def test_projection_preserves_exceptions_and_deduplicates_same_principle(store,state):
    source,event=add_match(store,state,0)
    m=Memories(store)
    claim='低价值手面对立直时重新比较追打收益'
    conditions=['高价值好型听牌仍比较进攻收益']
    m.revise({'claim':claim,'conditions':conditions,'supports':[event]},source.match_id)
    teach(store,claim=claim,conditions=conditions)
    selected=recall_learning(store,m.freeze(),state)
    assert len(selected)==1
    projection=learning_projection(selected)
    assert projection[0]['conditions']==conditions
    text=json.dumps(projection,ensure_ascii=False)
    assert 'synthetic-admin-input' not in text and 'source_quote' not in text and 'supports' not in text
    assert len(text)<700


async def test_production_coordinator_delivers_teaching_and_legal_actions_in_same_request(store,state):
    tip=teach(store,applies_to={'menzen':True})
    captured=[]
    class Model:
        async def decide(self,context,deadline):
            captured.append(context)
            decision,action=context.validate({'q':context.q,'a':next(iter(context.actions)),'r':'比较效率与防守','p':None})
            return decision,action,{}
    c=Coordinator(Settings(base_seconds=300),SimpleNamespace(store=store,state=state),Model())
    c.enabled=True
    state.operation.received_mono=time.monotonic()
    await c.decide(state)
    assert c.status=='shadow_decision_recorded'
    assert captured[0].data['memories'][0]['id']==tip['id']
    assert captured[0].data['memories'][0]['status']=='taught_unverified'
    assert {a.id for a in captured[0].actions.values()}=={a.id for a in legal_actions(state)}
    recall=store.records('memory_recall')[0]['payload']
    assert recall['revisions']==[{'id':tip['id'],'revision':1,'source':'admin_teaching'}]
    assert recall['projection_chars']>0
    c.settings.memory_recall_enabled=False
    await c.decide(state)
    assert captured[-1].data['memories']==[]


async def test_reviewer_reads_exact_injected_teaching_and_never_rewrites_it(store,state):
    source,_=add_match(store,state,0)
    tip=teach(store)
    store.record('memory_recall',{'revisions':[{'id':tip['id'],'revision':1,'source':'admin_teaching'}]},source.match_id)
    Knowledge(store).write({'id':tip['id'],'revision':1,'claim':'新的适用边界'},source_quote='新的适用边界',source_ref='new')
    async def request(payload,**kwargs):
        assert payload['learned_tips_used'][0]['claim']==tip['claim']
        assert payload['learned_tips_used'][0]['revision']==1
        return {'summary':'教学仅作未验证参考','lessons':[]},{}
    await Reviewer(store,SimpleNamespace(request=request),lambda:False).review(source.match_id)
    assert Knowledge(store).get(tip['id'])['revision']==2


def test_operation_contract_has_no_claimed_administrator_flag():
    request=OperationRequest(command_id='teaching',command={'action':'knowledge_write',
        'source_quote':'教学摘录','proposal':{'claim':'短麻将原则'}})
    assert isinstance(request.command.proposal,KnowledgeProposal)
    with pytest.raises(ValueError):
        OperationRequest(command_id='forged',command={'action':'knowledge_write','administrator':True,
            'source_quote':'教学摘录','proposal':{'claim':'短麻将原则'}})
