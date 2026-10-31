"""Independent catalog value checks against the pinned scoring library."""
from mahjong.hand_calculating.yaku_config import YakuConfig
import pytest

from majsoul_llm.domain import Meld, Phase, Snapshot
from majsoul_llm.rules import legal_actions, score
from majsoul_llm.yaku import YakuReference


LIBRARY_YAKU = {
    'riichi':'riichi','double_riichi':'daburu_riichi','ippatsu':'ippatsu',
    'menzen_tsumo':'tsumo','tanyao':'tanyao','pinfu':'pinfu','iipeikou':'iipeiko',
    'yakuhai_haku':'haku','yakuhai_hatsu':'hatsu','yakuhai_chun':'chun',
    'yakuhai_seat_wind':'yakuhai_place','yakuhai_round_wind':'yakuhai_round',
    'chankan':'chankan','rinshan_kaihou':'rinshan','haitei_raoyue':'haitei',
    'houtei_raoyui':'houtei','chiitoitsu':'chiitoitsu','toitoi':'toitoi',
    'sanankou':'sanankou','sankantsu':'sankantsu','sanshoku_doukou':'sanshoku_douko',
    'sanshoku_doujun':'sanshoku','ittsuu':'ittsu','chanta':'chantai','honroutou':'honroto',
    'shousangen':'shosangen','honitsu':'honitsu','junchan':'junchan','ryanpeikou':'ryanpeiko',
    'chinitsu':'chinitsu','kokushi_musou':'kokushi','suuankou':'suuankou',
    'daisangen':'daisangen','shousuushii':'shosuushi','daisuushii':'daisuushi',
    'tsuuiisou':'tsuisou','ryuuiisou':'ryuisou','chinroutou':'chinroto',
    'chuuren_poutou':'chuuren_poutou','suukantsu':'suukantsu','tenhou':'tenhou',
    'chiihou':'chiihou','suuankou_tanki':'suuankou_tanki',
    'kokushi_13_wait':'daburu_kokushi','junsei_chuuren_poutou':'daburu_chuuren_poutou',
}


@pytest.mark.parametrize('key,library_key',LIBRARY_YAKU.items())
def test_catalog_han_and_yakuman_match_library(key,library_key):
    entry=YakuReference().entries[key]
    yaku=getattr(YakuConfig(),library_key)
    if yaku.is_yakuman:
        assert entry['category']=='yakuman'
        assert entry['yakuman_multiplier']==yaku.han_closed//13
        assert entry['closed_han'] is None and entry['open_han'] is None
    else:
        assert entry['category']=='normal'
        assert (entry['closed_han'],entry['open_han'])==(yaku.han_closed,yaku.han_open)
    assert entry['requires_closed']==(yaku.han_open is None)


def test_bonus_limits_and_unverified_special_settlement_are_not_normal_yaku():
    entries=YakuReference().entries
    assert set(entries)-set(LIBRARY_YAKU)=={'dora','aka_dora','ura_dora','kazoe_yakuman','nagashi_mangan'}
    for key in ('dora','aka_dora','ura_dora'):
        assert entries[key]['category']=='bonus'
        assert not entries[key]['satisfies_win_yaku_requirement']
    assert entries['kazoe_yakuman']['category']=='score_limit'
    assert entries['nagashi_mangan']['category']=='special_settlement'
    assert entries['nagashi_mangan']['verification_status']=='partially_verified'
    for special,basic in (('suuankou_tanki','suuankou'),('kokushi_13_wait','kokushi_musou'),
                          ('junsei_chuuren_poutou','chuuren_poutou')):
        assert basic in entries[special]['replaces']


def test_ron_completed_triplet_is_not_counted_as_concealed_triplet():
    state=Snapshot(match_id='constructed-ron-triplet',self_seat=1)
    hand='2m 2m 2m 3p 3p 3p 4s 4s 5s 6s 7s 5p 5p'.split()
    ron=score(state,hand,'4s')
    tsumo=score(state,hand,'4s',tsumo=True)
    assert ron['legal_yaku'] and tsumo['legal_yaku']
    assert 'San Ankou' not in ron['yaku'] and 'San Ankou' in tsumo['yaku']


def test_server_win_option_is_preserved_when_local_value_cannot_confirm_yaku(state):
    state.players[0].melds=[Meld(id='open',actor=0,kind='chi',tiles=['1m','2m','3m'],event_id='open')]
    state.hand='3p 4p 5p 4s 5s 6s 6s 7s 8s 2p'.split()
    state.phase=Phase.RESPONSE
    state.context['last_discard']={'tile':'2p','seat':3,'event_id':'offer'}
    state.operation.raw['operation_list']=[{'type':9}]
    actions=legal_actions(state)
    assert [a.kind for a in actions]==['ron','pass']
    assert actions[0].features['server_offered_win']
    assert actions[0].features['value']['error']=='no_yaku'
