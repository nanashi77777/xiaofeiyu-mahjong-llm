"""Auditable experience revisions, separate from objective game facts."""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext

from .store import Store, packed, utcnow
from .domain import ExperienceRevision, MemoryScope, StylePreference


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

    def revise(self, proposal, match_id, *, catalog_version=None, commit=True):
        allowed = {"id", "revision", "status", "claim", "conditions", "supports", "counterexamples", "reviewed_matches", "reason", "yaku_ids", 'kind', 'applies_to', 'preference'}
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
        kind = proposal.get('kind',(previous or {}).get('kind','lesson'))
        scope = MemoryScope.model_validate(proposal.get('applies_to',(previous or {}).get('applies_to',{}))).model_dump()
        preference = proposal.get('preference',(previous or {}).get('preference'))
        if proposal.get('kind') == 'lesson':
            preference = None
        if preference is not None:
            preference = StylePreference.model_validate(preference).model_dump()
        if kind not in ('lesson','style') or (kind == 'style') != (preference is not None):
            raise ValueError('style requires a bounded preference; lessons cannot set one')
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
        for event_id in proposal.get("supports", []) + proposal.get("counterexamples", []):
            row = self.store.db.execute("SELECT match_id FROM events WHERE id=?", (event_id,)).fetchone()
            if not row or row[0] != match_id:
                raise ValueError("memory support is not from reviewed match")
        reviewed = set((previous or {}).get("reviewed_matches", []))
        if proposal.get("supports") or proposal.get("counterexamples"):
            reviewed.add(match_id)
        changed_claim = bool(previous and (claim != previous['claim'] or
                              proposal.get('conditions',previous['conditions']) != previous['conditions']
                              or kind != previous.get('kind','lesson')
                              or scope != MemoryScope.model_validate(previous.get('applies_to',{})).model_dump()
                              or preference != previous.get('preference')))
        if changed_claim and status != 'inactive':
            if status != 'candidate' or not proposal.get('supports'):
                raise ValueError('changed claim requires new evidence and candidate review')
            reviewed = {match_id}
            supports = list(dict.fromkeys(proposal['supports']))
        if status == "stable":
            original = (previous or {}).get("origin_match", match_id)
            if len(reviewed - {original}) < 3 or "counterexamples" not in proposal:
                raise ValueError("stable memory needs three subsequent distinct matches and counterexample check")
            origin = self.store.db.execute('SELECT MIN(received_at) FROM events WHERE match_id=?',(original,)).fetchone()[0]
            subsequent = [self.store.db.execute('SELECT MIN(received_at) FROM events WHERE match_id=?',(mid,)).fetchone()[0]
                          for mid in reviewed-{(previous or {}).get('origin_match',match_id)}]
            if not origin or any(not timestamp or timestamp <= origin for timestamp in subsequent):
                raise ValueError('stable memory review matches must actually follow its source match')
        memory = {"schema_version":"mahjong.memory.v1","id": id, "revision": (previous or {}).get("revision", 0) + 1, "status": status, "claim": claim,
                  "conditions": proposal.get("conditions", (previous or {}).get('conditions',[])), "supports": supports,
                  'kind':kind, 'applies_to':scope, 'preference':preference,
                  "counterexamples": list(dict.fromkeys((previous or {}).get('counterexamples',[])+proposal.get("counterexamples", []))),
                  "reviewed_matches": sorted(reviewed),
                  "origin_match": match_id if changed_claim else (previous or {}).get("origin_match", match_id),
                  "catalog_version": catalog_version or (previous or {}).get('catalog_version'),
                  "yaku_ids": proposal.get("yaku_ids", (previous or {}).get('yaku_ids',[])),
                  "reason": proposal.get("reason", ""), "updated_at": utcnow()}
        memory = ExperienceRevision.model_validate(memory).model_dump(mode='json')
        with self.store.db if commit else nullcontext():
            self.store.db.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (id, memory["revision"], status, utcnow(), packed(memory)))
        return memory

    def freeze(self):
        # Freezing eligibility is independent of any one hand. Retrieval later
        # selects a small relevant subset, without choosing or ranking actions.
        return self.query(limit=50)

    def recall(self, frozen, state, yaku_ids=(), limit=4):
        matched = []
        menzen = all(m.closed for m in state.players[state.self_seat].melds)
        threat = any(p.riichi != 'none' for seat,p in enumerate(state.players) if seat != state.self_seat)
        rank = state.ranks[state.self_seat] if state.ranks else None
        for memory in frozen:
            current = self.get(memory['id'])
            if not current or current['status'] == 'inactive':
                continue
            if any(current.get(key) != memory.get(key) for key in ('claim','conditions','kind','applies_to','preference')):
                continue
            scope = MemoryScope.model_validate(memory.get('applies_to',{}))
            tests = [(scope.phases,state.phase.value in scope.phases),
                     (scope.ranks,rank in scope.ranks),
                     (scope.menzen is not None,scope.menzen == menzen),
                     (scope.opponent_riichi is not None,scope.opponent_riichi == threat),
                     (scope.room_type is not None,scope.room_type == state.rules.room_type),
                     (scope.yaku_ids,bool(set(scope.yaku_ids) & set(yaku_ids)))]
            if any(bool(enabled) and not result for enabled,result in tests):
                continue
            matched.append((sum(bool(enabled) for enabled,_ in tests),memory))
        matched.sort(key=lambda item:(item[1]['status'] == 'stable',item[0],item[1]['updated_at']),reverse=True)
        selected, styles = [], set()
        for _,memory in matched:
            preference = memory.get('preference')
            if preference and preference['axis'] in styles:
                continue
            if preference:
                styles.add(preference['axis'])
            selected.append(memory)
            if len(selected) == limit:
                break
        return selected

    @staticmethod
    def projection(memories):
        return [{**{key:m.get(key) for key in ('id','revision','status','kind','claim','conditions','applies_to','preference','yaku_ids')},
                 'support_count':len(m['supports']), 'counterexample_count':len(m['counterexamples'])} for m in memories]

    def history(self, id):
        return [json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM memories WHERE id=? ORDER BY revision DESC", (id,))]
