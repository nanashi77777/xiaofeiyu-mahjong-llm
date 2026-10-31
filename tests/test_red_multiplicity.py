import pytest

from conftest import event
from majsoul_llm.domain import Meld, Snapshot
from majsoul_llm.rules import score, visible_counts
from majsoul_llm.state import apply_event


def test_two_reds_of_same_suit_add_real_bonus_without_breaking_physical_allocation():
    state=Snapshot(match_id='constructed-double-red',self_seat=1)
    hand='2m 3m 4m 3p 4p 0p 4s 5s 6s 6s 7s 0p 2p'.split()
    plain=score(state,[t.replace('0','5') for t in hand],'8s')
    red=score(state,hand,'8s')
    assert plain['legal_yaku'] and red['legal_yaku']
    assert red['han']==plain['han']+2 and 'Aka Dora' in red['yaku']
    assert red['cost']['main']>plain['cost']['main']
    state.rules.aka_dora=False
    disabled=score(state,hand,'8s')
    assert disabled['han']==plain['han'] and disabled['cost']==plain['cost']


def test_open_red_meld_bonus_and_reds_never_create_missing_yaku():
    state=Snapshot(match_id='constructed-open-reds',self_seat=1)
    meld=Meld(id='p',actor=1,kind='pon',tiles=['0p','0p','5p'],event_id='p')
    state.players[1].melds=[meld]
    hand='2m 3m 4m 4s 5s 6s 6s 7s 8s 2p'.split()
    scored=score(state,hand,'2p')
    assert scored['legal_yaku'] and scored['han']==3
    state.rules.open_tanyao=False
    no_yaku=score(state,hand,'2p')
    assert not no_yaku['legal_yaku'] and no_yaku['error']=='no_yaku'


def test_yakuman_does_not_gain_red_bonus():
    state=Snapshot(match_id='constructed-red-yakuman',self_seat=1)
    hand='0m 5m 5m 2p 2p 2p 3s 3s 3s 4s 4s 4s 5z'.split()
    red=score(state,hand,'5z',tsumo=True)
    plain=score(state,[t.replace('0','5') for t in hand],'5z',tsumo=True)
    assert red['han']>=13 and red['han']==plain['han']
    assert red['cost']==plain['cost'] and 'Aka Dora' not in red['yaku']


@pytest.mark.parametrize('tile',['0p','5p'])
def test_opponent_concealed_five_kan_retains_type_and_marks_color_unknown(state,tile):
    before=visible_counts(state)
    result=apply_event(state,event('ActionAnGangAddGang',{'seat':1,'type':3,'tiles':tile},2))
    meld=result.players[1].melds[0]
    assert meld.tiles==['5p']*4 and meld.red_count_unknown
    assert visible_counts(result)[13]==before[13]+4
    assert result.hand==state.hand


def test_no_red_rule_and_own_kan_preserve_known_color_identities(state):
    state.rules.aka_dora=False
    other=apply_event(state,event('ActionAnGangAddGang',{'seat':1,'type':3,'tiles':'5p'},2))
    assert not other.players[1].melds[0].red_count_unknown
    state.hand='0p 5p 5p 5p 1m 2m 3m 4m 5m 6m 6s 7s 8s 1z'.split()
    state.rules.aka_dora=True
    own=apply_event(state,event('ActionAnGangAddGang',{'seat':0,'type':3,'tiles':'5p'},2))
    assert own.players[0].melds[0].tiles==['0p','5p','5p','5p']
    assert not own.players[0].melds[0].red_count_unknown
