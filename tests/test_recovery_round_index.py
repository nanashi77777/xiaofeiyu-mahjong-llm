"""An observed round start, rather than the previous cache, defines its index."""
import pytest

from majsoul_llm.domain import Event, Snapshot
from majsoul_llm.engine import Engine
from majsoul_llm.state import apply_event

from conftest import HAND


def start(ju=0, ben=0):
    return {'tiles':HAND, 'scores':[25000]*4, 'chang':0, 'ju':ju, 'ben':ben}


@pytest.mark.parametrize('dealer_repeat', [False, True])
async def test_reconnect_into_unseen_round_increments_once_and_replays(store, dealer_repeat):
    engine = Engine(store)
    engine.state = Snapshot(match_id='test', self_seat=0)
    await engine.action('ActionNewRound', start(), 0)
    first = engine.state.round_uid
    await engine.interrupt('fixture_disconnect')
    restored = start(0, 1) if dealer_repeat else start(1)
    actions = [{'name':'ActionNewRound', 'step':0, 'data':restored}]
    await engine.restore({'actions':actions})
    assert engine.state.round_seq == 2 and engine.state.round_uid != first
    count = store.next_seq('test')
    for _ in range(3):
        await engine.restore({'actions':actions})
        assert engine.state.round_seq == 2 and store.next_seq('test') == count
    restarted = Engine(store)
    restarted.state = store.latest('test')
    await restarted.restore({'actions':actions})
    assert restarted.state.round_seq == 2 and store.next_seq('test') == count
    replayed = Snapshot(match_id='test', self_seat=0)
    for event in store.events('test'):
        replayed = apply_event(replayed, Event.model_validate({**event, 'received_mono':0}))
    assert replayed.round_seq == restarted.state.round_seq
    assert [r[0] for r in store.db.execute('SELECT seq FROM rounds ORDER BY seq')] == [1,2]


async def test_existing_round_recovery_uses_events_even_if_legacy_cache_is_wrong(store):
    engine = Engine(store)
    engine.state = Snapshot(match_id='test', self_seat=0)
    await engine.action('ActionNewRound', start(), 0)
    await engine.action('ActionNewRound', start(1), 0)
    engine.state.round_seq = 1
    store.db.execute('UPDATE rounds SET seq=1')
    await engine.restore({'actions':[{'name':'ActionNewRound', 'step':0, 'data':start(1)}]})
    assert engine.state.round_seq == 2
    assert store.next_seq('test') == 3
    await engine.action('ActionNewRound', start(2), 0)
    assert engine.state.round_seq == 3


async def test_first_observed_late_round_remains_partial_without_guessing_missing_rounds(store):
    engine = Engine(store)
    engine.state = Snapshot(match_id='test', self_seat=0)
    data = {**start(2), 'chang':1}
    await engine.restore({'actions':[{'name':'ActionNewRound', 'step':0, 'data':data}]})
    assert engine.state.round_seq == 1
    assert engine.state.completeness == 'partial'
    assert engine.state.wind == 1 and engine.state.hand_number == 3
