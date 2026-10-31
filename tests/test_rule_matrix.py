import pytest

from conftest import HAND, event
from majsoul_llm.domain import Discard, Meld, Phase, Snapshot
from majsoul_llm.engine import Engine
from majsoul_llm.rules import hand_features, legal_actions, score
from majsoul_llm.state import apply_event


@pytest.mark.parametrize('stack,double,han',[(True,True,65),(True,False,39),(False,True,26),(False,False,13)])
def test_yakuman_stacking_and_double_variants(stack,double,han):
    state=Snapshot(match_id='matrix',self_seat=1)
    state.rules.composite_yakuman=stack
    state.rules.double_yakuman=double
    hand='1z 1z 1z 2z 2z 2z 3z 3z 3z 4z 4z 4z 5z'.split()
    value=score(state,hand,'5z')
    assert value['legal_yaku'] and value['han']==han
    assert value['cost']['main']==32000*(han//13)


@pytest.mark.parametrize('enabled',[True,False])
def test_open_tanyao_is_a_real_rule_and_dora_does_not_replace_it(enabled):
    state=Snapshot(match_id='matrix',self_seat=1,dora=['1m'])
    state.rules.open_tanyao=enabled
    state.players[1].melds=[Meld(id='m',actor=1,kind='chi',tiles=['2m','3m','4m'],event_id='m')]
    value=score(state,'3p 4p 5p 4s 5s 6s 6s 7s 8s 2p'.split(),'2p')
    assert value['legal_yaku']==enabled
    if not enabled:
        assert value['error']=='no_yaku'


@pytest.mark.parametrize('enabled,main',[(False,7700),(True,8000)])
def test_kiriage_mangan(enabled,main):
    state=Snapshot(match_id='matrix',self_seat=1,dora=['1p'])
    state.rules.kiriage=enabled
    value=score(state,'2m 3m 4m 3p 4p 5p 4s 5s 6s 6s 7s 2p 2p'.split(),'8s')
    assert value['han']==4 and value['fu']==30 and value['cost']['main']==main


@pytest.mark.parametrize('enabled,main',[(False,24000),(True,32000)])
def test_counted_yakuman_limit(enabled,main):
    state=Snapshot(match_id='matrix',self_seat=1,dora=['7m'])
    state.rules.counted_yakuman=enabled
    value=score(state,'2m 2m 3m 3m 4m 4m 5m 5m 6m 6m 7m 7m 8m'.split(),'8m')
    assert value['legal_yaku'] and value['han']>=13 and value['cost']['main']==main


def test_red_bonus_and_event_yaku_require_observed_context():
    state=Snapshot(match_id='matrix',self_seat=1,hand=HAND,remaining=0)
    state.context={'calls_seen':False,'draw_counts':[0,1,0,0]}
    value=score(state,HAND[:-1],'1z',tsumo=True,actual=True)
    assert 'Chiihou' in value['yaku'] and value['han']>=13
    state.context['calls_seen']=True
    value=score(state,HAND[:-1],'1z',tsumo=True,actual=True)
    assert 'Chiihou' not in value['yaku'] and 'Haitei Raoyue' in value['yaku']
    state.context['rinshan']=True
    value=score(state,HAND[:-1],'1z',tsumo=True,actual=True)
    assert 'Rinshan Kaihou' in value['yaku'] and 'Haitei Raoyue' not in value['yaku']
    state.remaining=20
    state.context['last_kan']={'type':2,'seat':2}
    value=score(state,HAND[:-1],'1z',actual=True)
    assert 'Chankan' in value['yaku']
    hand='2m 3m 4m 3p 4p 0p 4s 5s 6s 6s 7s 2p 2p'.split()
    state.context={}
    red=score(state,hand,'8s')
    state.rules.aka_dora=False
    plain=score(state,hand,'8s')
    assert red['han']==plain['han']+1


@pytest.mark.parametrize('riichi',[False,True])
async def test_only_accepted_ron_skip_sets_furiten_and_draw_only_clears_temporary(store,state,riichi):
    state.hand=HAND[:-1]
    state.phase=Phase.RESPONSE
    state.players[0].concealed_count=13
    state.operation.raw['operation_list']=[{'type':9}]
    state.players[0].riichi='established' if riichi else 'none'
    engine=Engine(store)
    engine.state,engine.connection=state,'ours'
    engine.input_windows[('ours',5)]=state.operation.window_id
    response={'method':'.lq.FastTest.inputChiPengGang','direction':'response','connection':'ours',
              'request_id':5,'data':{},'original_input':{'cancel_operation':True}}
    await engine.message(response)
    assert engine.state.furiten['riichi' if riichi else 'temporary']
    assert engine.state.operation is None
    count=len(store.events('test'))
    await engine.message(response)
    assert len(store.events('test'))==count
    state=apply_event(engine.state,event('ActionDealTile',{'seat':0,'tile':'1z'},3))
    assert not state.furiten['temporary'] and state.furiten['riichi']==riichi


def test_all_red_pon_instances_and_discard_furiten(state):
    state.hand='5m 5m 0m 1p 2p 3p 6s 7s 8s 1z 1z 2z 3z'.split()
    state.phase=Phase.RESPONSE
    state.operation.raw['operation_list']=[{'type':3,'combination':['5m|5m','0m|5m']}]
    actions=legal_actions(state)
    assert {tuple(a.consume_indices) for a in actions if a.kind=='pon'}=={(0,1),(0,2),(1,2)}
    assert actions[-1].kind=='pass'
    state.hand=HAND[:-1]
    state.players[0].river=[Discard(tile='1z',event_id='r',seq=1)]
    assert hand_features(state,state.hand)['discard_furiten']
