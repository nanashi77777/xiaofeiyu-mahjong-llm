import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import event
from majsoul_llm.outcomes import win_outcomes, win_summary
from majsoul_llm.queries import Queries
from majsoul_llm.review import REVIEW_INPUT_VERSION, Reviewer
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store, packed


def rows(*actions):
    return [dict(id=f'e{i}', match_id='test', round_uid='test:r1', seq=i, kind=kind, data=data)
            for i, (kind, data) in enumerate(actions, 1)]


@pytest.mark.parametrize(('source','win','seat','kind','tile'), [
    (('ActionDiscardTile', {'seat':1,'tile':'3s'}), {'seat':0,'hu_tile':'3s'}, 1, 'discard', '3s'),
    (('ActionDiscardTile', {'tile':'0m'}), {'seat':2,'hu_tile':'0m','baopai':1}, 0, 'discard', '0m'),
    (('ActionAnGangAddGang', {'seat':0,'type':2,'tiles':'0p'}), {'seat':3,'hu_tile':'0p'}, 0, 'kakan', '0p'),
    (('ActionAnGangAddGang', {'seat':1,'type':3,'tiles':'5s'}), {'hu_tile':'0s'}, 1, 'ankan', '0s'),
    (('ActionDiscardTile', {'seat':1,'tile':'0m'}), {}, 1, 'discard', '0m'),
])
def test_ron_source_and_red_tile(source, win, seat, kind, tile):
    outcome, = win_outcomes(rows(source, ('ActionHule', {'hules':[win]})))
    assert (outcome['from_seat'], outcome['source_kind'], outcome['tile']) == (seat, kind, tile)
    assert outcome['source_event_id'] == 'e1' and outcome['source_status'] == 'confirmed'
    assert outcome['kind'] == 'ron' and outcome['issues'] == []


@pytest.mark.parametrize(('source','win','tile'), [
    (('ActionDealTile', {'tile':'0s'}), {'zimo':True,'hu_tile':'0s'}, '0s'),
    (('ActionDealTile', {'seat':2}), {'seat':2,'zimo':True,'hu_tile':'0p'}, '0p'),
    (('ActionNewRound', {'ju':1,'tiles':['1m','2m']}), {'seat':1,'zimo':True,'hu_tile':'9p'}, '9p'),
])
def test_tsumo_including_hidden_draw_and_initial_dealer(source, win, tile):
    outcome, = win_outcomes(rows(source, ('ActionHule', {'hules':[win], 'delta_scores':[-8000,16000,-4000,-4000]})))
    assert outcome['kind'] == 'tsumo' and outcome['tile'] == tile
    assert outcome['from_seat'] is None and outcome['source_status'] == 'confirmed'
    summary = win_summary([outcome], 0)
    assert summary['deal_in_events'] == summary['deal_in_winners'] == 0
    assert summary['tsumo'] == int(win.get('seat', 0) == 0)


def test_multi_ron_counts_one_deal_in_and_each_winner_and_ignores_payments():
    outcomes = win_outcomes(rows(
        ('ActionDiscardTile', {'tile':'7z'}),
        ('ObservedPass', {'declined_ron':False}),
        ('ActionHule', {'hules':[{'seat':1,'hu_tile':'7z'}, {'seat':3,'hu_tile':'7z','baopai':2}],
                        'delta_scores':[-24000,12000,0,12000]})))
    assert [w['winner_seat'] for w in outcomes] == [1,3]
    assert {w['source_event_id'] for w in outcomes} == {'e1'}
    assert win_summary(outcomes, 0) == dict(self_seat=0,ron=0,tsumo=0,deal_in_events=1,
                                          deal_in_winners=2,unattributed_ron=0)
    assert win_summary(outcomes, 2)['deal_in_events'] == 0


@pytest.mark.parametrize(('actions','issue'), [
    ([('ActionHule', {'hules':[{'seat':1,'hu_tile':'3s'}], 'delta_scores':[-8000,8000,0,0]})], 'source_not_observed'),
    ([('ActionDiscardTile', {'tile':'3s'}), ('ActionDealTile', {'seat':2}),
      ('ActionHule', {'hules':[{'seat':1,'hu_tile':'3s'}]})], 'source_action_mismatch'),
    ([('ActionDiscardTile', {'tile':'3s'}), ('ActionDiscardTile', {'seat':2,'tile':'4s'}),
      ('ActionHule', {'hules':[{'seat':1,'hu_tile':'3s'}]})], 'source_tile_mismatch'),
    ([('ActionDiscardTile', {'tile':'3s'}), ('ActionHule', {'hules':[{'hu_tile':'3s'}]})], 'source_seat_mismatch'),
    ([('ActionDealTile', {'seat':1}), ('ActionHule', {'hules':[{'zimo':True,'hu_tile':'3s'}]})], 'source_seat_mismatch'),
    ([('ActionDiscardTile', {'tile':'0s'}), ('ActionHule', {'hules':[{'seat':1,'hu_tile':'5s'}]})], 'source_tile_mismatch'),
    ([('ActionDiscardTile', {'tile':'3s'}), ('ActionHule', {'hules':[{'seat':1,'hu_tile':'bad'}]})], 'winning_tile_invalid'),
])
def test_incomplete_or_contradictory_results_never_invent_deal_in(actions, issue):
    outcome, = win_outcomes(rows(*actions))
    assert issue in outcome['issues'] and outcome['source_status'] != 'confirmed'
    assert outcome['from_seat'] is None
    assert win_summary([outcome], 0)['deal_in_events'] == 0


@pytest.mark.parametrize('boundary', ['ActionNoTile','ActionLiuJu','NotifyGameEndResult',
                                    'NotifyGameTerminate','ActionChiPengGang','ActionFuture'])
def test_terminal_call_and_unknown_events_invalidate_source(boundary):
    outcome, = win_outcomes(rows(('ActionDiscardTile', {'tile':'3s'}), (boundary, {}),
                                 ('ActionHule', {'hules':[{'seat':1,'hu_tile':'3s'}]})))
    assert outcome['source_event_id'] is None and outcome['source_status'] == 'unknown'


@pytest.mark.parametrize('change', ['gap','round','match','missing_round'])
def test_no_source_across_missing_messages_or_rounds(change):
    stream = rows(('ActionDiscardTile', {'tile':'3s'}), ('ActionHule', {'hules':[{'seat':1,'hu_tile':'3s'}]}))
    key, value = {'gap':('seq',9), 'round':('round_uid','test:r2'), 'match':('match_id','other'),
                  'missing_round':('round_uid',None)}[change]
    stream[1][key] = value
    assert win_outcomes(stream)[0]['from_seat'] is None


def test_new_round_and_prior_hule_cannot_supply_ron_and_duplicate_is_idempotent():
    stream = rows(('ActionDiscardTile', {'tile':'3s'}), ('ActionHule', {'hules':[{'seat':1,'hu_tile':'3s'}]}),
                  ('ActionHule', {'hules':[{'seat':2,'hu_tile':'3s'}]}), ('ActionNewRound', {}),
                  ('ActionHule', {'hules':[{'seat':3,'hu_tile':'3s'}]}))
    outcomes = win_outcomes([stream[0], stream[0], *stream[1:]])
    assert len(outcomes) == 3
    assert [w['from_seat'] for w in outcomes] == [0,None,None]


def test_concealed_kan_without_revealed_color_does_not_invent_normal_five():
    outcome, = win_outcomes(rows(('ActionAnGangAddGang', {'seat':1,'type':3,'tiles':'0p'}),
                                 ('ActionHule', {'hules':[{}]})))
    assert outcome['source_kind'] == 'ankan' and outcome['from_seat'] == 1
    assert outcome['tile'] is None and outcome['issues'] == ['winning_tile_not_observed']


def archived_match(store, state):
    store.save_event(event('ActionNewRound', {'tiles':state.hand}), state)
    def append(kind, data, seq):
        nonlocal state
        e = event(kind, data, seq)
        state = apply_event(state, e)
        store.save_event(e, state)
        return state
    # Cross the public event pagination limit before the winning action.
    for seq in range(2, 205):
        append('ObservedPass', {'declined_ron':False}, seq)
    append('ActionDiscardTile', {'seat':1,'tile':'0p'}, 205)
    append('ActionHule', {'hules':[{'count':6,'fu':30,'hu_tile':'0p','point_sum':12000}],
                           'scores':[37000,13000,25000,25000]}, 206)
    first_round = state.round_uid
    append('ActionNewRound', {'tiles':state.hand, 'ju':1}, 207)
    append('ActionDiscardTile', {'tile':'1m'}, 208)
    append('ActionHule', {'hules':[{'seat':1,'hu_tile':'1m'},{'seat':2,'hu_tile':'1m'}]}, 209)
    return state, first_round


def test_existing_archive_queries_pagination_privacy_and_restart(store, state):
    state, round_id = archived_match(store, state)
    expected = store.win_outcomes(state.match_id)
    assert len(expected) == 3 and expected[0]['source_event_id'] == 'e205'
    query = Queries(store, 'secret')
    first = query.round(round_id, limit=50)['data']
    last = query.round(round_id, offset=200, limit=50)['data']
    assert first['wins'] == last['wins'] == expected[:1]
    assert 'hu_tile' not in packed(first['events'])
    assert query.match(state.match_id)['data']['win_summary']['deal_in_events'] == 1
    assert query.match(state.match_id)['data']['win_summary']['deal_in_winners'] == 2
    assert query.big_hands()['data'][0]['outcome'] == expected[0]
    new = event('ActionNewRound', {'tiles':state.hand}, 210)
    state = apply_event(state, new)
    store.save_event(new, state)
    active = query.round(state.round_uid)['data']
    assert active['wins'] == [] and active['decisions'] == [] and active['result'] is None
    assert 'tiles' not in packed(active['events'])
    event_count = store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    store.save_event(new, state)
    assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == event_count
    path = store.db.execute('PRAGMA database_list').fetchone()['file']
    reopened = Store(Path(path))
    try:
        assert reopened.win_outcomes(state.match_id) == expected
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert 'from_seat' not in {r[1] for r in reopened.db.execute('PRAGMA table_info(wins)')}
    finally:
        reopened.close()


async def test_review_receives_attribution_and_versioned_dedup_without_auto_reprocessing(store, state):
    state, _ = archived_match(store, state)
    state.lifecycle = 'interrupted'
    store.save_snapshot(state)
    events = store.events(state.match_id, limit=200) + store.events(state.match_id, offset=200, limit=200)
    legacy = hashlib.sha256(packed(events).encode()).hexdigest()
    store.record('review', {'fingerprint':legacy,'summary':'old'}, state.match_id)
    store.record('review_job', {'status':'complete','source_key':state.last_event_id,'attempt':1}, state.match_id)
    calls = []
    async def request(payload, **kwargs):
        calls.append(payload)
        assert '自摸付款和包牌不算放铳' in kwargs['system']
        return {'summary':'按当时可知事实检查追打风险','lessons':[]}, {}
    reviewer = Reviewer(store, SimpleNamespace(request=request), lambda:False)
    try:
        await reviewer.resume_pending()
        await reviewer.enqueue(state.match_id)
        assert reviewer.queue.empty() and calls == []
        await reviewer.review(state.match_id)  # Explicit new-format review; not a mass replay.
        await reviewer.review(state.match_id)
        assert len(calls) == 1
        payload = calls[0]
        assert payload['input_version'] == REVIEW_INPUT_VERSION
        assert len(payload['events']) == 209 and len(payload['win_outcomes']) == 3
        assert payload['win_summary']['ron'] == 1
        assert payload['win_summary']['deal_in_events'] == 1 and payload['win_summary']['deal_in_winners'] == 2
        record = store.records('review', state.match_id)[0]['payload']
        assert record['input_version'] == REVIEW_INPUT_VERSION and record['fingerprint'] != legacy
        assert not store.records('review_proposal', state.match_id)[0]['payload']['output']['lessons']
    finally:
        reviewer.worker.cancel()
        await asyncio.gather(reviewer.worker, return_exceptions=True)
