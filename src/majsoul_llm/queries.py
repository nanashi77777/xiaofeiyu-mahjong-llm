"""Read-only public projections. Active concealed information never leaves here."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

from .memory import Memories
from .outcomes import win_summary
from .store import Store, packed, utcnow


class Queries:
    def __init__(self, store: Store, secret: str, capture_health=None, recovery_status=None):
        self.store, self.secret = store, secret.encode()
        self.capture_health = capture_health
        self.recovery_status = recovery_status

    def cursor(self, data):
        raw = base64.urlsafe_b64encode(packed(data).encode()).rstrip(b"=")
        mac = hmac.new(self.secret, raw, hashlib.sha256).hexdigest().encode()
        return (raw + b"." + mac).decode()

    def uncursor(self, cursor, kind):
        if not isinstance(cursor, str) or len(cursor) > 12000:
            raise ValueError("invalid cursor")
        raw, signature = cursor.encode().rsplit(b".", 1)
        if not hmac.compare_digest(hmac.new(self.secret, raw, hashlib.sha256).hexdigest().encode(), signature):
            raise ValueError("invalid cursor")
        value = json.loads(base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4)))
        if value.get("kind") != kind:
            raise ValueError("cursor type mismatch")
        return value

    def scope(self, cursor=None):
        if cursor:
            return self.uncursor(cursor, "scope")["ids"], cursor
        ids = [s.match_id for s in self.store.recent()]
        return ids, self.cursor({"kind":"scope", "ids":ids, "as_of":utcnow()})

    def envelope(self, data, *, state=None, next_cursor=None, **extra):
        age = None
        if state and state.observed_at:
            age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(state.observed_at)).total_seconds())
        health = self.capture_health() if self.capture_health else None
        live = bool(state and state.lifecycle == "active" and health
                    and health["connected"] and health["match_id"] == state.match_id)
        return {"schema_version":"mahjong.query.v1", "as_of":utcnow(), "source":"local_websocket_archive",
                "freshness":{"age_seconds":age, "status":"no_data" if not state else "live" if live else "last_observed",
                             "capture_connected":health["connected"] if health else False,
                             "capture_age_seconds":health["age_seconds"] if health else None},
                "completeness":state.completeness if state else "unknown", "data":data, "next_cursor":next_cursor, **extra}

    def summary(self, state):
        return {"match_id":state.match_id, "room_type":state.rules.room_type, "lifecycle":state.lifecycle,
                "completeness":state.completeness, "observed_at":state.observed_at, "self_seat":state.self_seat,
                "round":{"id":state.round_uid, "wind":"东南西北"[state.wind], "hand":state.hand_number, "honba":state.honba, "deposits":state.deposits},
                "last_observed":{"scores":state.scores, "ranks":state.ranks, "rank_basis":"points_initial_absolute_seat_tie_order"},
                "final_result":{"scores":state.final_scores, "ranks":state.final_ranks} if state.lifecycle == "ended" else None,
                "consistent":state.consistent, "unknown_fields":state.unknown_fields}

    def live(self):
        state = self.store.latest()
        extra = {'recovery':self.recovery_status()} if self.recovery_status else {}
        return self.envelope(self.summary(state) if state else None, state=state, **extra)

    def matches(self, *, scope_cursor=None, cursor=None, status=None, completeness=None, limit=5):
        if cursor and not scope_cursor:
            scope_cursor=self.uncursor(cursor,'matches')['scope']
        ids, scope_cursor = self.scope(scope_cursor)
        offset = self.uncursor(cursor, "matches")["offset"] if cursor else 0
        if cursor and self.uncursor(cursor, "matches")["scope"] != scope_cursor:
            raise ValueError("pagination scope changed")
        states = [s for id in ids if (s := self.store.latest(id)) is not None]
        selected = [s for s in states if (not status or s.lifecycle == status) and (not completeness or s.completeness == completeness)]
        page = selected[offset:offset+limit]
        next_cursor = self.cursor({"kind":"matches", "scope":scope_cursor, "offset":offset+limit}) if offset+limit < len(selected) else None
        return self.envelope([self.summary(s) for s in page], state=states[0] if states else None,
                             next_cursor=next_cursor, scope_cursor=scope_cursor, total=len(selected))

    def match(self, match_id):
        state = self.store.latest(match_id)
        if not state:
            raise KeyError("match not found")
        outcomes = self.store.win_outcomes(match_id)
        rounds = [{"id":r["id"], "sequence":r["seq"], "status":r["status"], "has_result":bool(r["result_event_id"]),
                   "wins":[w for w in outcomes if w['round_id'] == r['id'] and r['result_event_id']]}
                  for r in self.store.db.execute("SELECT id,seq,status,result_event_id FROM rounds WHERE match_id=? ORDER BY seq", (match_id,))]
        return self.envelope({**self.summary(state), "rounds":rounds,
                              "win_summary":win_summary(outcomes, state.self_seat)}, state=state)

    def round(self, round_id, offset=0, limit=50):
        row = self.store.db.execute("SELECT * FROM rounds WHERE id=?", (round_id,)).fetchone()
        if not row:
            raise KeyError("round not found")
        state = self.store.latest(row["match_id"])
        current = state.round_uid == round_id and state.lifecycle != "ended" and row['status'] != 'round_end'
        events = self.store.events(state.match_id, round_id, offset, limit)
        if current:
            # Build from an explicit allowlist; no raw payload, explanation, plan or citations.
            allowed = {"seat", "type", "moqie", "is_liqi", "is_wliqi", "doras", "scores", "left_tile_count", "liqi"}
            projected = []
            for event in events:
                data = {k:v for k,v in event["data"].items() if k in allowed}
                if event["kind"] == "ActionDiscardTile":
                    data["tile"] = event["data"].get("tile")
                if event["kind"] in ("ActionChiPengGang", "ActionAnGangAddGang"):
                    data.update({k:event["data"][k] for k in ("tiles", "froms") if k in event["data"]})
                projected.append({"id":event["id"], "kind":event["kind"], "seq":event["seq"], "data":data})
            events = projected
        else:
            # Match/round/source format is shared by the enclosing response.
            # Keep the same small shape as the active projection for bot archives.
            events = [{k:e[k] for k in ("id", "seq", "kind", "data")} for e in events]
        decisions = [] if current else [{"id":r["id"], "decision":r["payload"].get("decision"),
                                         "action":r["payload"].get("action"),
                                         "annotation_status":r["payload"].get("annotation_status","complete")}
                                        for r in self.store.decisions(state.match_id, 1000)
                                        if r["round_uid"] == round_id]
        result = self.store.event(row["result_event_id"]) if not current and row["result_event_id"] else None
        return self.envelope({"round_id":round_id, "match_id":state.match_id, "events":events, "decisions":decisions,
                              "active_private_fields_hidden":current, "result":result.data if result else None,
                              "wins":[] if current else self.store.win_outcomes(state.match_id, round_id)},
                             state=state, next_cursor=str(offset+limit) if len(events) == limit else None)

    def big_hands(self, scope_cursor=None, offset=0, limit=10):
        ids, scope_cursor = self.scope(scope_cursor)
        states = {id:self.store.latest(id) for id in ids}
        wins = []
        outcomes = {w['id']:w for id in ids for w in self.store.win_outcomes(id)}
        for row in self.store.db.execute("SELECT * FROM wins ORDER BY rowid DESC"):
            state = states.get(row["match_id"])
            if not state or state.self_seat != row["seat"]:
                continue
            han = row["han"]
            if not row["yakuman"] and (han is None or han < 6):
                continue
            event = self.store.event(row["event_id"])
            win = event.data["hules"][row["win_index"]]
            wins.append({"id":row["id"], "match_id":row["match_id"], "round_id":row["round_uid"], "room_type":state.rules.room_type,
                         "level":"yakuman" if row["yakuman"] else "sanbaiman" if han >= 11 else "baiman" if han >= 8 else "haneman",
                         "han":han, "fu":row["fu"], "tsumo":win.get("zimo", False), "fans":win.get("fans", []),
                         "points":win.get("point_sum"), "source":"server_hule",
                         "outcome":outcomes.get(row['id'])})
        return self.envelope(wins[offset:offset+limit], scope_cursor=scope_cursor,
                             next_cursor=str(offset+limit) if offset+limit < len(wins) else None, total=len(wins))

    def memories(self, query="", status=None, id=None, offset=0, limit=8):
        memory = Memories(self.store)
        active_ids = {r[0] for r in self.store.db.execute("SELECT id FROM matches WHERE lifecycle!='ended'")}
        def public(m):
            # A resumed reviewed game can make previously historical prose private again.
            return not (set(m.get("reviewed_matches", [])) | {m.get("origin_match")}) & active_ids
        if id:
            current = memory.get(id)
            if not current or not public(current):
                return self.envelope(None)
            history = [m for m in memory.history(id) if public(m)]
            return self.envelope({"current":current,"revisions":history[offset:offset+limit]}, next_cursor=str(offset+limit) if offset+limit<len(history) else None)
        result = [m for m in memory.query(query, status, 50) if public(m)]
        return self.envelope(result[offset:offset+limit], next_cursor=str(offset+limit) if offset+limit<len(result) else None)
