"""Verify a backed-up v1 archive on a disposable copy before migrating live data."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from majsoul_llm.domain import Event, Snapshot
from majsoul_llm.store import Store


def content_bytes(db):
    total = 0
    for table, names in {"events":("payload","meta"), "matches":("snapshot",),
                         "rounds":("snapshot","result"), "wins":("payload",)}.items():
        present = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for name in present.intersection(names):
            total += db.execute(f"SELECT COALESCE(SUM(LENGTH(CAST({name} AS BLOB))),0) FROM {table}").fetchone()[0]
    return total


def verify(path: Path):
    source = sqlite3.connect(path)
    try:
        with tempfile.TemporaryDirectory(prefix="mahjong-archive-") as folder:
            copy = Path(folder) / "archive.sqlite"
            db = sqlite3.connect(copy)
            source.backup(db)
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("verification expects an unmigrated v1 archive")
            before = content_bytes(db)
            snapshots = {r[0]:Snapshot.model_validate_json(r[1]).model_dump(mode="json")
                         for r in db.execute("SELECT id,snapshot FROM matches")}
            events = {r[0]:Event.model_validate_json(r[1]).model_dump(mode="json", exclude={"historical","received_mono"})
                      for r in db.execute("SELECT id,payload FROM events")}
            db.close()
            store = Store(copy)
            try:
                for key, expected in snapshots.items():
                    if store.latest(key).model_dump(mode="json") != expected:
                        raise ValueError("latest snapshot changed during migration")
                for key, expected in events.items():
                    if store.event(key).model_dump(mode="json", exclude={"historical","received_mono"}) != expected:
                        raise ValueError("event changed during migration")
                if store.db.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("archive has invalid foreign keys")
                after = content_bytes(store.db)
                return {"status":"equivalent", "matches":len(snapshots), "events":len(events),
                        "before_json_bytes":before, "after_json_bytes":after,
                        "reduction_percent":round(100*(before-after)/before,2) if before else 0}
            finally:
                store.close()
    finally:
        source.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify(args.path)
    if args.report:
        args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps(report))
