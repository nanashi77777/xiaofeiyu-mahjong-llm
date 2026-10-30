import time

import pytest

from majsoul_llm.domain import Event, Snapshot
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store, utcnow


HAND = "1m 2m 3m 4m 5m 6m 2p 3p 4p 6s 7s 8s 1z 1z".split()


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.sqlite")
    yield db
    db.close()


def event(kind, data, seq=1, historical=False):
    return Event(id=f"e{seq}", match_id="test", kind=kind, seq=seq, data=data,
                 received_at=utcnow(), received_mono=time.monotonic(), source="fixture", historical=historical)


@pytest.fixture
def state():
    return apply_event(Snapshot(match_id="test", self_seat=0), event("ActionNewRound", {
        "tiles": HAND, "scores": [25000] * 4, "doras": ["9p"], "left_tile_count": 69,
        "operation": {"seat": 0, "operation_list": [{"type": 1, "combination": []}]}}))
