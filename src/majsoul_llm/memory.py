"""Auditable experience revisions, separate from objective game facts."""
from __future__ import annotations

import json
import uuid

from .store import Store, packed, utcnow


class Memories:
    def __init__(self, store: Store):
        self.store = store

    def get(self, id):
        row = self.store.db.execute("SELECT payload FROM memories WHERE id=? ORDER BY revision DESC LIMIT 1", (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def query(self, query="", status=None, limit=8):
        rows = self.store.db.execute("SELECT m.payload FROM memories m JOIN (SELECT id,MAX(revision) r FROM memories GROUP BY id) v ON m.id=v.id AND m.revision=v.r ORDER BY m.created_at DESC").fetchall()
        result = [json.loads(row[0]) for row in rows]
        words = query.casefold().split()
        return [m for m in result if (m["status"] == status if status else m["status"] != "inactive")
                and (not words or any(w in packed(m).casefold() for w in words))][:min(limit, 50)]

    def revise(self, proposal, match_id, *, catalog_version=None):
        allowed = {"id", "revision", "status", "claim", "conditions", "supports", "counterexamples", "reviewed_matches", "reason", "yaku_ids"}
        if set(proposal) - allowed:
            raise ValueError("memory update has forbidden fields")
        reviewed_state=self.store.latest(match_id)
        if not reviewed_state or reviewed_state.lifecycle=='active':
            raise ValueError("memory revisions require an ended or interrupted recorded match")
        id = proposal.get("id") or uuid.uuid4().hex
        previous = self.get(id)
        if previous and proposal.get("revision") != previous["revision"]:
            raise ValueError("memory revision conflict")
        status = proposal.get("status", "candidate")
        if status not in ("candidate", "stable", "inactive"):
            raise ValueError("invalid memory status")
        for key in ('conditions','supports','counterexamples','yaku_ids'):
            if key in proposal and (not isinstance(proposal[key],list) or len(proposal[key])>40 or any(not isinstance(v,str) or len(v)>500 for v in proposal[key])):
                raise ValueError("invalid memory evidence list")
        if proposal.get('yaku_ids'):
            from .yaku import YakuReference
            YakuReference().read(ids=proposal['yaku_ids'])
        if not previous and (status!='candidate' or not proposal.get('supports')):
            raise ValueError('new experience requires source evidence and candidate status')
        claim = proposal.get("claim", previous.get("claim", "") if previous else "")
        if not isinstance(claim, str) or not claim or len(claim) > 1200:
            raise ValueError("invalid memory claim")
        supports = list(dict.fromkeys((previous or {}).get("supports", []) + proposal.get("supports", [])))
        for event_id in proposal.get("supports", []):
            row = self.store.db.execute("SELECT match_id FROM events WHERE id=?", (event_id,)).fetchone()
            if not row or row[0] != match_id:
                raise ValueError("memory support is not from reviewed match")
        reviewed = set((previous or {}).get("reviewed_matches", []))
        reviewed.add(match_id)
        if status == "stable":
            original = (previous or {}).get("origin_match", match_id)
            if len(reviewed - {original}) < 3 or "counterexamples" not in proposal:
                raise ValueError("stable memory needs three subsequent distinct matches and counterexample check")
            origin_state=self.store.latest(original)
            if not origin_state or any(not (s:=self.store.latest(mid)) or not s.observed_at or s.observed_at<=origin_state.observed_at for mid in reviewed-{original}):
                raise ValueError('stable memory review matches must actually follow its source match')
        memory = {"schema_version":"mahjong.memory.v1","id": id, "revision": (previous or {}).get("revision", 0) + 1, "status": status, "claim": claim,
                  "conditions": proposal.get("conditions", []), "supports": supports,
                  "counterexamples": proposal.get("counterexamples", []), "reviewed_matches": sorted(reviewed),
                  "origin_match": (previous or {}).get("origin_match", match_id), "catalog_version": catalog_version,
                  "yaku_ids": proposal.get("yaku_ids", []), "reason": proposal.get("reason", ""), "updated_at": utcnow()}
        with self.store.db:
            self.store.db.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (id, memory["revision"], status, utcnow(), packed(memory)))
        return memory

    def history(self, id):
        return [json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM memories WHERE id=? ORDER BY revision DESC", (id,))]
