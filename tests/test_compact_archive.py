import json
import sqlite3

import pytest

from conftest import event
from majsoul_llm.domain import Snapshot
from majsoul_llm.engine import Engine
from majsoul_llm.queries import Queries
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store, packed


def scene(state):
    result = state.model_dump(mode="json")
    if result.get("operation"):
        # Archive events cannot reopen live deadlines from a past process.
        result["operation"].pop("received_mono")
        result["operation"].pop("historical")
    return result


async def test_compact_archive_replays_rounds_red_calls_riichi_multiwin_and_end(tmp_path):
    path = tmp_path / "archive.sqlite"
    store = Store(path)
    engine = Engine(store)
    engine.state = Snapshot(match_id="test", self_seat=0)
    hand = "0m 5m 5m 1m 2m 3m 1p 2p 3p 6s 7s 8s 1z 1z".split()
    actions = [
        ("ActionNewRound", {"tiles":hand, "scores":[25000]*4, "md5":"round1", "doras":["9p"]}, 0),
        ("ActionDiscardTile", {"seat":0, "tile":"1z"}, 1),
        ("ActionDealTile", {"seat":1}, 2),
        ("ActionDiscardTile", {"seat":1, "tile":"5m", "moqie":True}, 3),
        ("ActionChiPengGang", {"seat":0, "type":1, "tiles":["0m","5m","5m"], "froms":[0,0,1]}, 4),
        ("ActionDiscardTile", {"seat":0, "tile":"1m"}, 5),
        ("ActionHule", {"scores":[37000,21000,21000,21000], "hules":[{"seat":0,"count":6,"fu":30,"point_sum":12000}]}, 6),
        ("ActionNewRound", {"tiles":hand, "scores":[37000,21000,21000,21000], "md5":"round2", "ju":1, "ben":2}, 0),
        ("ActionDiscardTile", {"seat":0,"tile":"1z","is_liqi":True}, 1),
        ("ActionDealTile", {"seat":1,"liqi":{"seat":0,"score":36000,"liqibang":1}}, 2),
        ("ActionDealTile", {"seat":0,"tile":"0p"}, 3),
        ("ActionDiscardTile", {"seat":0,"tile":"0p","moqie":True}, 4),
        ("ActionHule", {"scores":[24000,33000,31000,12000], "hules":[{"seat":1,"count":6,"fu":30},{"seat":2,"count":8,"fu":30}]}, 5),
        ("NotifyGameEndResult", {"result":{"players":[{"seat":s,"part_point_1":p} for s,p in [(1,33000),(2,31000),(0,24000),(3,12000)]]}}, None),
    ]
    expected = []
    for kind, data, step in actions:
        await engine.action(kind, data, step)
        assert engine.last_error is None
        expected.append(scene(engine.state))
        assert scene(store.latest()) == expected[-1]
    rows = store.db.execute("SELECT payload FROM events").fetchall()
    assert all("schema_version" not in json.loads(r[0]) and "match_id" not in json.loads(r[0]) for r in rows)
    assert "public_events" not in json.loads(store.db.execute("SELECT snapshot FROM matches").fetchone()[0])
    assert "snapshot" not in {r[1] for r in store.db.execute("PRAGMA table_info(rounds)")}
    assert "payload" not in {r[1] for r in store.db.execute("PRAGMA table_info(wins)")}
    q = Queries(store, "fixture")
    assert q.big_hands()["total"] == 1
    assert q.match("test")["data"]["final_result"]["ranks"] == [3,1,2,4]
    assert q.round(expected[6]["round_uid"])["data"]["result"] == actions[6][1]
    store.close()

    store = Store(path)
    replay = Engine(store)
    replay.state = Snapshot(match_id="test", self_seat=0)
    for index, (kind, data, step) in enumerate(actions):
        await replay.action(kind, data, step, historical=True)
        assert scene(replay.state) == expected[index]
    assert len(store.events("test")) == len(actions)
    store.close()


def test_v1_archive_backup_migration_and_interrupted_query(tmp_path, state):
    path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE matches(id TEXT PRIMARY KEY,observed_at TEXT,lifecycle TEXT,completeness TEXT,snapshot TEXT);
        CREATE TABLE events(id TEXT PRIMARY KEY,match_id TEXT,round_uid TEXT,seq INTEGER,kind TEXT,payload TEXT,received_at TEXT);
        CREATE TABLE rounds(id TEXT PRIMARY KEY,match_id TEXT,seq INTEGER,status TEXT,snapshot TEXT,result TEXT);
        CREATE TABLE wins(id TEXT PRIMARY KEY,match_id TEXT,round_uid TEXT,seat INTEGER,han INTEGER,fu INTEGER,yakuman INTEGER,payload TEXT);
        PRAGMA user_version=1;
    """)
    first = event("ActionNewRound", {"tiles":state.hand,"scores":[25000]*4,"custom_future_fact":{"value":False}})
    final = event("ActionHule", {"hules":[{"seat":0,"count":6,"fu":30,"point_sum":12000}],"scores":[37000,21000,21000,21000]}, 2)
    state = apply_event(Snapshot(match_id="test",self_seat=0),first)
    state = apply_event(state, final)
    state.lifecycle = "interrupted"
    db.execute("INSERT INTO matches VALUES(?,?,?,?,?)", (state.match_id,state.observed_at,state.lifecycle,state.completeness,packed(state)))
    for e in (first,final):
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)", (e.id,e.match_id,state.round_uid,e.seq,e.kind,packed(e),e.received_at))
    db.execute("INSERT INTO rounds VALUES(?,?,?,?,?,?)", (state.round_uid,state.match_id,1,"round_end",packed(state),packed(final.data)))
    db.execute("INSERT INTO wins VALUES(?,?,?,?,?,?,?,?)", ("e2:0","test",state.round_uid,0,6,30,0,packed(final.data["hules"][0])))
    db.commit()
    db.close()

    broken = tmp_path / "broken.sqlite"
    original = sqlite3.connect(path)
    bad = sqlite3.connect(broken)
    original.backup(bad)
    original.close()
    bad.execute("UPDATE events SET payload='{}' WHERE id='e2'")
    bad.commit()
    bad.close()
    with pytest.raises(ValueError):
        Store(broken)
    bad = sqlite3.connect(broken)
    assert bad.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "meta" not in {r[1] for r in bad.execute("PRAGMA table_info(events)")}
    bad.close()

    store = Store(path)
    assert scene(store.latest()) == scene(state)
    assert store.event(first.id).data == first.data
    assert store.event(first.id).source == "fixture"
    assert store.event(first.id).historical and store.event(first.id).received_mono == 0
    q = Queries(store, "fixture")
    assert q.big_hands()["data"][0]["points"] == 12000
    assert q.round(state.round_uid)["data"]["result"] == final.data
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    backups = list(tmp_path.glob("legacy.sqlite*.bak"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
    assert json.loads(backup.execute("SELECT payload FROM events WHERE id='e1'").fetchone()[0]) == first.model_dump(mode="json")
    backup.close()
    store.close()
    store = Store(path)
    assert scene(store.latest()) == scene(state)
    assert len(list(tmp_path.glob("legacy.sqlite*.bak"))) == 1
    store.close()
