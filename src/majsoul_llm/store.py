"""Transactional authoritative archive and separate decision/experience records."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import Event, Snapshot
from .state import public_event


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def packed(value):
    return json.dumps(value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
                      ensure_ascii=False, separators=(",", ":"))


def snapshot_cache(state: Snapshot) -> str:
    # A single replaceable latest-state cache; history lives in events only.
    return packed(state.model_dump(mode="json", exclude_defaults=True, exclude={"public_events"}))


def event_meta(event: Event) -> str | None:
    meta = {}
    if event.source_step is not None:
        meta["step"] = event.source_step
    if event.source != "websocket":
        meta["source"] = event.source
    return packed(meta) if meta else None


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        if version>2:
            self.db.close()
            raise ValueError('archive schema is newer than this program')
        if version == 1:
            try:
                self._migrate_v1(path)
            except Exception:
                self.db.close()
                raise
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, lifecycle TEXT NOT NULL,
          completeness TEXT NOT NULL, snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, match_id TEXT NOT NULL, round_uid TEXT,
          seq INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL,meta TEXT,
          FOREIGN KEY(match_id) REFERENCES matches(id));
        CREATE INDEX IF NOT EXISTS events_by_match ON events(match_id, seq);
        CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY,match_id TEXT NOT NULL,seq INTEGER NOT NULL,
          status TEXT NOT NULL,result_event_id TEXT REFERENCES events(id),FOREIGN KEY(match_id) REFERENCES matches(id));
        CREATE TABLE IF NOT EXISTS wins(id TEXT PRIMARY KEY,match_id TEXT NOT NULL,round_uid TEXT NOT NULL,
          seat INTEGER NOT NULL,han INTEGER,fu INTEGER,yakuman INTEGER NOT NULL DEFAULT 0,
          event_id TEXT NOT NULL REFERENCES events(id),win_index INTEGER NOT NULL,
          FOREIGN KEY(match_id) REFERENCES matches(id),FOREIGN KEY(round_uid) REFERENCES rounds(id));
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,match_id TEXT,round_uid TEXT,purpose TEXT NOT NULL,
          created_at TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memories(id TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL,
          created_at TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(id,revision));
        PRAGMA user_version=2;
        """)

    def _migrate_v1(self, path: Path):
        """Backup first; schema and content changes commit or roll back together."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = sqlite3.connect(path.with_name(path.name + ".before-v2-" + stamp + ".bak"))
        try:
            self.db.backup(backup)
        finally:
            backup.close()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("ALTER TABLE events ADD COLUMN meta TEXT")
            for row in self.db.execute("SELECT id,payload FROM events").fetchall():
                event = Event.model_validate_json(row["payload"])
                self.db.execute("UPDATE events SET payload=?,meta=? WHERE id=?",
                                (packed(event.data), event_meta(event), row["id"]))
            for row in self.db.execute("SELECT id,snapshot FROM matches").fetchall():
                state = Snapshot.model_validate_json(row["snapshot"])
                self.db.execute("UPDATE matches SET snapshot=? WHERE id=?", (snapshot_cache(state), row["id"]))
            self.db.execute("ALTER TABLE rounds DROP COLUMN snapshot")
            self.db.execute("ALTER TABLE rounds RENAME COLUMN result TO result_event_id")
            for row in self.db.execute("SELECT id FROM rounds").fetchall():
                result = self.db.execute("SELECT id FROM events WHERE round_uid=? AND kind IN "
                                         "('ActionHule','ActionNoTile','ActionLiuJu') ORDER BY seq DESC LIMIT 1",
                                         (row["id"],)).fetchone()
                self.db.execute("UPDATE rounds SET result_event_id=? WHERE id=?",
                                (result[0] if result else None, row["id"]))
            self.db.execute("ALTER TABLE wins ADD COLUMN event_id TEXT REFERENCES events(id)")
            self.db.execute("ALTER TABLE wins ADD COLUMN win_index INTEGER")
            for row in self.db.execute("SELECT id FROM wins").fetchall():
                event_id, index = row["id"].rsplit(":", 1)
                self.db.execute("UPDATE wins SET event_id=?,win_index=? WHERE id=?", (event_id, int(index), row["id"]))
            self.db.execute("ALTER TABLE wins DROP COLUMN payload")
            self.db.execute("PRAGMA user_version=2")

    def has_event(self, event_id):
        return self.db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone() is not None

    def event(self, event_id):
        row = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._event(row) if row else None

    @staticmethod
    def _event(row) -> Event:
        meta = json.loads(row["meta"]) if row["meta"] else {}
        return Event(id=row["id"], match_id=row["match_id"], seq=row["seq"], kind=row["kind"],
                     data=json.loads(row["payload"]), received_at=row["received_at"],
                     source_step=meta.get("step"), source=meta.get("source", "websocket"),
                     received_mono=0, historical=True)

    def _snapshot(self, raw) -> Snapshot:
        state = Snapshot.model_validate_json(raw)
        query, args = "SELECT * FROM events WHERE match_id=?", [state.match_id]
        if state.round_uid:
            query += " AND round_uid=?"
            args.append(state.round_uid)
        query += " AND seq<=? ORDER BY seq"
        state.public_events = [public_event(self._event(row)) for row in self.db.execute(query, args + [state.state_version])]
        return state

    def next_seq(self, match_id):
        return self.db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE match_id=?", (match_id,)).fetchone()[0]

    def save_snapshot(self, state):
        with self.db:
            self.db.execute("INSERT INTO matches VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                            "observed_at=excluded.observed_at,lifecycle=excluded.lifecycle,completeness=excluded.completeness,snapshot=excluded.snapshot",
                            (state.match_id, state.observed_at or utcnow(), state.lifecycle, state.completeness, snapshot_cache(state)))

    def save_event(self, event: Event, state: Snapshot):
        with self.db:
            self.db.execute("INSERT INTO matches VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                            "observed_at=excluded.observed_at,lifecycle=excluded.lifecycle,completeness=excluded.completeness,snapshot=excluded.snapshot",
                            (state.match_id, event.received_at, state.lifecycle, state.completeness, snapshot_cache(state)))
            self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)",
                            (event.id, event.match_id, state.round_uid, event.seq, event.kind, packed(event.data), event.received_at, event_meta(event)))
            if state.round_uid:
                self.db.execute("INSERT INTO rounds VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                                "status=excluded.status,result_event_id=COALESCE(excluded.result_event_id,rounds.result_event_id)",
                                (state.round_uid, state.match_id, state.round_seq, state.phase.value,
                                 event.id if event.kind in ("ActionHule", "ActionNoTile", "ActionLiuJu") else None))
            if event.kind == "ActionHule" and state.round_uid:
                for index, win in enumerate(event.data.get("hules", [])):
                    self.db.execute("INSERT OR IGNORE INTO wins VALUES(?,?,?,?,?,?,?,?,?)",
                                    (event.id + f":{index}", state.match_id, state.round_uid, win.get("seat", 0),
                                     win.get("count"), win.get("fu"), int(win.get("yiman", False)), event.id, index))

    def latest(self, match_id=None) -> Snapshot | None:
        row = self.db.execute("SELECT snapshot FROM matches WHERE id=?", (match_id,)).fetchone() if match_id else self.db.execute("SELECT snapshot FROM matches ORDER BY observed_at DESC LIMIT 1").fetchone()
        return self._snapshot(row[0]) if row else None

    def recent(self, limit=20, ids: list[str] | None = None):
        rows = self.db.execute("SELECT snapshot FROM matches ORDER BY observed_at DESC LIMIT ?", (min(limit, 20),)).fetchall()
        return [self._snapshot(row[0]) for row in rows if ids is None or json.loads(row[0])["match_id"] in ids]

    def events(self, match_id, round_uid=None, offset=0, limit=100):
        query, args = "SELECT * FROM events WHERE match_id=?", [match_id]
        if round_uid:
            query += " AND round_uid=?"
            args.append(round_uid)
        args += [min(limit, 200), offset]
        return [self._event(row).model_dump(mode="json", exclude={"received_mono"})
                for row in self.db.execute(query + " ORDER BY seq LIMIT ? OFFSET ?", args)]

    def record(self, purpose: str, payload: dict[str, Any], match_id=None, round_uid=None, id=None):
        id = id or uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)", (id, match_id, round_uid, purpose, utcnow(), packed(payload)))
        return id

    def records(self, purpose, match_id=None, limit=100):
        sql, params = "SELECT * FROM records WHERE purpose=?", [purpose]
        if match_id:
            sql += " AND match_id=?"
            params.append(match_id)
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in self.db.execute(sql + " ORDER BY created_at DESC LIMIT ?", params + [limit])]

    def mark_interrupted(self):
        for state in self.recent():
            if state.lifecycle == "active":
                state.lifecycle = "interrupted"
                state.operation = None
                with self.db:
                    self.db.execute("UPDATE matches SET lifecycle=?,snapshot=? WHERE id=?", (state.lifecycle, snapshot_cache(state), state.match_id))

    def close(self):
        self.db.close()
