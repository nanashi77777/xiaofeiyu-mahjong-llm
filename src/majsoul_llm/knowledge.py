"""Administrator-taught Mahjong principles, with no invented game evidence."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from .domain import MemoryScope, Record, StylePreference
from .memory import Memories, MemoryClaim, MemoryConditions, MemoryReason, scope_relevance
from .store import packed, utcnow
from .yaku import YakuReference


class KnowledgeProposal(Record):
    id: str | None = Field(default=None,pattern=r'^tip_[a-f0-9]{32}$')
    revision: StrictInt | None = Field(default=None,ge=1)
    status: Literal['active','inactive'] | None = None
    claim: MemoryClaim | None = None
    conditions: MemoryConditions | None = None
    applies_to: MemoryScope | None = None
    kind: Literal['lesson','style'] | None = None
    preference: StylePreference | None = None
    topic: Literal['general','efficiency','defense','placement','yaku','calls','riichi'] | None = None
    yaku_ids: list[str] | None = Field(default=None,max_length=8)
    reason: MemoryReason = ''

    @model_validator(mode='after')
    def version_required(self):
        if (self.id is None) != (self.revision is None):
            raise ValueError('knowledge id and revision are required together')
        return self


def signature(memory):
    def text(s):
        return re.sub(r'[\W_]+','',unicodedata.normalize('NFKC',s).casefold())
    return hashlib.sha256(packed([text(memory['claim']),[text(s) for s in memory['conditions']],
        MemoryScope.model_validate(memory.get('applies_to',{})).model_dump(exclude_defaults=True),
        memory.get('kind','lesson'),memory.get('preference'),
        sorted(memory.get('yaku_ids',[]))]).encode()).hexdigest()


class Knowledge:
    def __init__(self,store):
        self.store=store

    def get(self,id,revision=None):
        row=self.store.db.execute('SELECT payload FROM mahjong_knowledge WHERE id=? '+
            ('AND revision=? ' if revision is not None else '')+'ORDER BY revision DESC LIMIT 1',
            (id,revision) if revision is not None else (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def query(self,query='',*,ids=(),include_inactive=False):
        rows=self.store.db.execute('SELECT k.payload FROM mahjong_knowledge k JOIN '
            '(SELECT id,MAX(revision) r FROM mahjong_knowledge GROUP BY id) v '
            'ON k.id=v.id AND k.revision=v.r ORDER BY k.rowid DESC').fetchall()
        result=[json.loads(row[0]) for row in rows]
        words=query.casefold().split()
        return [m for m in result if (include_inactive or m['status']=='active') and (not ids or m['id'] in ids)
            and (not words or any(w in packed([m['claim'],m['conditions'],m['topic'],m['yaku_ids']]).casefold() for w in words))]

    def public(self,memory):
        # Only Mahjong summaries. Source message digests, quotations and identities stay local.
        return {key:value for key,value in memory.items() if key not in ('source_ref','source_digest')}

    def read(self,query='',ids=()):
        rows=self.query(query,ids=ids,include_inactive=True)
        revisions=[]
        if len(ids)==1:
            revisions=[self.public(json.loads(row[0])) for row in self.store.db.execute(
                'SELECT payload FROM mahjong_knowledge WHERE id=? ORDER BY revision DESC LIMIT 50',(ids[0],))]
        return {'schema_version':'mahjong.knowledge.v1','items':[self.public(m) for m in rows[:50]],
                'revisions':revisions,
                'total':len(rows),'truncated':len(rows)>50,
                'next_step':'narrow topic or specify ids' if len(rows)>50 else None,
                'trust':'administrator_taught_unverified','writes':'current_direct_administrator_only'}

    def write(self,proposal,*,source_quote,source_ref):
        proposal=KnowledgeProposal.model_validate(proposal)
        if not isinstance(source_quote,str) or not 2<=len(source_quote.strip())<=600:
            raise ValueError('current direct teaching excerpt required')
        previous=self.get(proposal.id) if proposal.id else None
        if proposal.id and (not previous or previous['revision']!=proposal.revision):
            raise ValueError('knowledge revision conflict')
        status=proposal.status or (previous or {}).get('status','active')
        if not previous and (not proposal.claim or status!='active'):
            raise ValueError('new teaching requires a concise active claim')
        changes=proposal.model_dump(exclude_none=True,exclude={'id','revision','reason','status'})
        memory={**(previous or {'claim':'','conditions':[],'applies_to':{},'kind':'lesson','preference':None,
                    'topic':'general','yaku_ids':[]}),**changes}
        if proposal.kind=='lesson':
            memory['preference']=None
        if (memory['kind']=='style') != (memory['preference'] is not None):
            raise ValueError('style requires a bounded preference')
        if memory['yaku_ids']:
            YakuReference().read(ids=memory['yaku_ids'])
        memory['applies_to']=MemoryScope.model_validate(memory['applies_to']).model_dump(exclude_defaults=True)
        digest=signature(memory)
        for other in self.query():
            if status=='active' and other['id']!=proposal.id and signature(other)==digest:
                return {**self.public(other),'deduplicated':True}
        memory.update(schema_version='mahjong.knowledge.v1',id=proposal.id or 'tip_'+uuid.uuid4().hex,
            revision=(previous or {}).get('revision',0)+1,status=status,
            trust='taught_unverified',reason=proposal.reason,updated_at=utcnow(),
            source_ref=source_ref,source_digest=hashlib.sha256(source_quote.encode()).hexdigest())
        with self.store.db:
            self.store.db.execute('INSERT INTO mahjong_knowledge VALUES(?,?,?,?,?)',
                (memory['id'],memory['revision'],memory['status'],utcnow(),packed(memory)))
        return self.public(memory)


def recall_learning(store,frozen,state,yaku_ids=(),*,limit=8):
    """One small shared budget, without changing candidate actions or making strategy decisions."""
    if type(limit) is not int or not 0<=limit<=8:
        raise ValueError('invalid shared memory limit')
    if not limit:
        return []
    experiences=Memories(store).recall(frozen,state,yaku_ids,limit=8)
    taught=[m for m in Knowledge(store).query() if scope_relevance(m['applies_to'],state,yaku_ids) is not None]
    # Archived v1 lessons predate structured scopes and style preferences.
    # Hydrate only this read projection; retain their full text and revision.
    pool=[{'kind':'lesson','applies_to':{},'preference':None,**m,'source':'experience'} for m in experiences]
    pool += [{**m,'source':'admin_teaching'} for m in taught]
    pool.sort(key=lambda m:(scope_relevance(m['applies_to'],state,yaku_ids),m.get('status')=='stable',
                            m['updated_at']),reverse=True)
    result,seen,styles=[],set(),set()
    for memory in pool:
        # Exact normalized principles only; exceptions and scopes participate in the signature.
        digest=signature(memory)
        axis=(memory.get('preference') or {}).get('axis')
        if digest in seen or axis and axis in styles:
            continue
        seen.add(digest)
        if axis:
            styles.add(axis)
        result.append(memory)
        if len(result)==limit:
            break
    return result


def learning_projection(selected):
    output=[]
    for m in selected:
        if m['source']=='experience':
            item=Memories.projection([m])[0]
        else:
            item={key:m[key] for key in ('id','revision','kind','claim','conditions','preference','yaku_ids')}
            item['applies_to']=m['applies_to']
            item['status']='taught_unverified'
        item['source']=m['source']
        # Omit empty/default fields, preserving every constraint, exception and old long claim.
        item['applies_to']=MemoryScope.model_validate(item['applies_to']).model_dump(exclude_defaults=True)
        output.append({k:v for k,v in item.items() if v is not None and v!=[] and v!={}})
    return output


def learning_status(store):
    tips=Knowledge(store).query(include_inactive=True)
    recent=store.records('memory_recall',limit=30)
    return {'active_teachings':sum(m['status']=='active' for m in tips),
            'inactive_teachings':sum(m['status']=='inactive' for m in tips),
            'experience_count':len(Memories(store).query(limit=None)),
            'effective':'new teachings at next decision snapshot; inactive versions excluded from future retrieval',
            'injection_is_not_proof_of_adoption':True,
            'recent_injections':[{'match_id':r['match_id'],'round_uid':r['round_uid'],
                'stage':r['payload'].get('stage','legacy_selection'),
                'at':r['created_at'],'revisions':r['payload'].get('revisions',[]),
                'projection_chars':r['payload'].get('projection_chars'),
                'enabled':r['payload'].get('enabled',True)} for r in recent]}
