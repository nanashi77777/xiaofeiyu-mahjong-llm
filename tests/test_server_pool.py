import pytest

from majsoul_llm.timing import TimeBank


def op(state,window,reported):
    return state.operation.model_copy(update={'window_id':window,'fixed_ms':5000,'add_ms':reported})


def test_fresh_server_report_corrects_confirmation_delay_without_repeating_same_window(state):
    bank=TimeBank(bank=10,margin=.8)
    bank.enter_round('r1')
    first=op(state,'first',10000)
    start=first.received_mono
    assert bank.deadline(first,now=start,verified=True)==pytest.approx(start+14.2)
    # Server confirmation arrives long after input stopped the actual clock.
    bank.charge('first',15.61)
    assert bank.remaining==pytest.approx(-.61)
    repeated=bank.deadline(first,now=start,verified=True)
    assert repeated==pytest.approx(start+4.2)
    actual=op(state,'next-fresh',3000)
    assert bank.deadline(actual,now=start,verified=True)==pytest.approx(start+7.2)
    assert bank.remaining==3 and bank.server_ceiling==3
    bank.charge('next-fresh',6)
    assert bank.remaining==2
    assert bank.deadline(actual,now=start,verified=True)==pytest.approx(start+6.2)


def test_increasing_or_historical_pool_never_refills_and_negative_estimate_keeps_base(state):
    bank=TimeBank(bank=10,margin=.8)
    bank.enter_round('r1')
    first=op(state,'first',3000)
    start=first.received_mono
    bank.deadline(first,now=start,verified=True)
    bank.charge('first',6)
    increased=op(state,'untrusted-increase',10000)
    assert bank.deadline(increased,now=start,verified=True)==pytest.approx(start+6.2)
    assert bank.server_ceiling==3 and bank.remaining==2
    with pytest.raises(ValueError,match='historical'):
        bank.deadline(increased.model_copy(update={'historical':True}),now=start,verified=True)
    bank.charge('untrusted-increase',8)
    assert bank.deadline(op(state,'no-report',None),now=start,verified=True)==pytest.approx(start+4.2)


def test_persisted_observed_ceiling_survives_restart_and_old_records_stay_conservative(state):
    bank=TimeBank(bank=10,margin=.8)
    bank.enter_round('r1')
    bank.restore({'remaining':-.61,'server_ceiling':3,'window_id':'previous'})
    assert bank.deadline(op(state,'new',3000),now=state.operation.received_mono,verified=True)==pytest.approx(state.operation.received_mono+7.2)
    bank.charge('new',6)
    restarted=TimeBank(bank=10,margin=.8)
    restarted.enter_round('r1')
    restarted.restore(bank.checkpoint('new'))
    assert restarted.charge('new',6)==2
    assert restarted.deadline(op(state,'new',3000),now=state.operation.received_mono,verified=True)==pytest.approx(state.operation.received_mono+6.2)
    assert restarted.deadline(op(state,'old-report',10000),now=state.operation.received_mono,verified=True)==pytest.approx(state.operation.received_mono+6.2)
    legacy=TimeBank(bank=10,margin=.8)
    legacy.enter_round('r1')
    legacy.restore({'remaining':-.61,'window_id':'old-archive'})
    assert legacy.deadline(op(state,'new',10000),now=state.operation.received_mono,verified=True)==pytest.approx(state.operation.received_mono+4.2)
    legacy.enter_round('r2')
    assert legacy.remaining==10 and legacy.server_ceiling==10
