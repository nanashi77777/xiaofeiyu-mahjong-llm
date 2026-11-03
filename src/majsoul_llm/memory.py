"""Auditable experience revisions, separate from objective game facts."""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from typing import Annotated

from pydantic import Field

from .store import Store, packed, utcnow
from .domain import ExperienceRevision, MemoryScope, Record, StylePreference

MEMORY_RECALL_LIMIT = 8
MemoryClaim = Annotated[str, Field(min_length=1, max_length=120)]
MemoryCondition = Annotated[str, Field(min_length=1, max_length=64)]
MemoryConditions = Annotated[list[MemoryCondition], Field(max_length=3)]
MemoryReason = Annotated[str, Field(max_length=160)]


class ConciseMemoryText(Record):
    """New/rewritten text limits; archived v1 memories remain readable intact."""
    claim: MemoryClaim
    conditions: MemoryConditions = Field(default_factory=list)
    reason: MemoryReason = ''


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
        selected=[m for m in result if (m["status"] == status if status else m["status"] != "inactive")
                  and (not words or any(w in packed(m).casefold() for w in words))]
        return selected if limit is None else selected[:min(limit, 50)]

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
        conditions = proposal.get('conditions', (previous or {}).get('conditions', []))
        reason = proposal.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 160:
            raise ValueError('memory revision reason must fit 160 characters')
        changed_text = not previous or claim != previous['claim'] or conditions != previous['conditions']
        if status != 'inactive' and changed_text:
            # Reject rather than truncate: a suffix may contain the exception
            # that makes a Mahjong lesson applicable. Historical text is only
            # rewritten by an explicit, evidenced model proposal.
            ConciseMemoryText(claim=claim, conditions=conditions, reason=reason)
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
                  "conditions": conditions, "supports": supports,
                  'kind':kind, 'applies_to':scope, 'preference':preference,
                  "counterexamples": list(dict.fromkeys((previous or {}).get('counterexamples',[])+proposal.get("counterexamples", []))),
                  "reviewed_matches": sorted(reviewed),
                  "origin_match": match_id if changed_claim else (previous or {}).get("origin_match", match_id),
                  "catalog_version": catalog_version or (previous or {}).get('catalog_version'),
                  "yaku_ids": proposal.get("yaku_ids", (previous or {}).get('yaku_ids',[])),
                  "reason": reason, "updated_at": utcnow()}
        memory = ExperienceRevision.model_validate(memory).model_dump(mode='json')
        with self.store.db if commit else nullcontext():
            self.store.db.execute("INSERT INTO memories VALUES(?,?,?,?,?)", (id, memory["revision"], status, utcnow(), packed(memory)))
        return memory

    def freeze(self):
        # Freezing eligibility is independent of any one hand. Retrieval later
        # selects a small relevant subset, without choosing or ranking actions.
        return [{'id':m['id'],'revision':m['revision']} for m in self.query(limit=None)]

    def recall(self, frozen, state, yaku_ids=(), limit=MEMORY_RECALL_LIMIT):
        if type(limit) is not int or limit < 0:
            raise ValueError('memory recall limit must be a nonnegative integer')
        limit = min(limit, MEMORY_RECALL_LIMIT)
        if limit == 0:
            return []
        matched = []
        current_versions={m['id']:m for m in self.query(limit=None)}
        for memory in frozen:
            current = current_versions.get(memory['id'])
            if not current or current['status'] == 'inactive':
                continue
            if 'claim' not in memory:
                if current['revision'] != memory['revision']:
                    # A reference pins the exact auditable revision; no mid-match upgrades.
                    row=self.store.db.execute('SELECT payload FROM memories WHERE id=? AND revision=?',
                                              (memory['id'],memory['revision'])).fetchone()
                    if not row:
                        continue
                    memory=json.loads(row[0])
                else:
                    memory=current
            if any(current.get(key) != memory.get(key) for key in ('claim','conditions','kind','applies_to','preference')):
                continue
            relevance=scope_relevance(memory.get('applies_to',{}),state,yaku_ids)
            if relevance is None:
                continue
            matched.append((relevance,memory))
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

    def disable(self, id, revision, reason):
        previous=self.get(id)
        if not previous or previous['revision'] != revision:
            raise ValueError('memory revision conflict')
        memory={**previous,'revision':revision+1,'status':'inactive','reason':validate_disable_reason(reason),
                'updated_at':utcnow()}
        with self.store.db:
            self.store.db.execute('INSERT INTO memories VALUES(?,?,?,?,?)',
                (id,memory['revision'],'inactive',utcnow(),packed(memory)))
        return memory

    @staticmethod
    def projection(memories):
        return [{**{key:m.get(key) for key in ('id','revision','status','kind','claim','conditions','applies_to','preference','yaku_ids')},
                 'support_count':len(m['supports']), 'counterexample_count':len(m['counterexamples'])} for m in memories]

    def history(self, id):
        return [json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM memories WHERE id=? ORDER BY revision DESC", (id,))]


def validate_disable_reason(reason):
    # Keep the same length/type boundary without constructing an unrelated claim.
    if not isinstance(reason,str) or not 1 <= len(reason) <= 160:
        raise ValueError('memory disable reason required')
    return reason


def scope_relevance(value, state, yaku_ids=()):
    scope=MemoryScope.model_validate(value)
    menzen=all(m.closed for m in state.players[state.self_seat].melds)
    threat=any(p.riichi!='none' for seat,p in enumerate(state.players) if seat!=state.self_seat)
    rank=state.ranks[state.self_seat] if state.ranks else None
    tests=[(bool(scope.phases),state.phase.value in scope.phases),(bool(scope.ranks),rank in scope.ranks),
           (scope.menzen is not None,scope.menzen==menzen),
           (scope.opponent_riichi is not None,scope.opponent_riichi==threat),
           (scope.room_type is not None,scope.room_type==state.rules.room_type),
           (bool(scope.yaku_ids),bool(set(scope.yaku_ids)&set(yaku_ids)))]
    gap=(state.scores[state.self_seat]-max(v for i,v in enumerate(state.scores) if i!=state.self_seat)
         if state.scores else None)
    for key,current in [('score_gap',gap),('discards',len(state.players[state.self_seat].river)),('remaining',state.remaining)]:
        low,high=getattr(scope,'min_'+key),getattr(scope,'max_'+key)
        tests += [(low is not None,current is not None and (low is None or current>=low)),
                  (high is not None,current is not None and (high is None or current<=high))]
    return None if any(enabled and not ok for enabled,ok in tests) else sum(enabled for enabled,_ in tests)
