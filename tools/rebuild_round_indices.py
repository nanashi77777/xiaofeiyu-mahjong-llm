"""Plan or explicitly rebuild verified round indices in ended local archives.

Only derived indices change. Original events, observations and validation reports
remain untouched. Plans, backups and actual match identifiers stay local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from majsoul_llm.domain import Snapshot
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store, packed, utcnow


def connect(path, *, writable=False):
    db = sqlite3.connect(path.resolve().as_uri()+('?mode=rw' if writable else '?mode=ro'),uri=True)
    db.row_factory = sqlite3.Row
    return db


def material(db, match_id):
    match = db.execute('SELECT * FROM matches WHERE id=?', (match_id,)).fetchone()
    if match is None:
        raise ValueError('planned_match_missing')
    events = db.execute('SELECT * FROM events WHERE match_id=? ORDER BY seq', (match_id,)).fetchall()
    rounds = db.execute('SELECT * FROM rounds WHERE match_id=? ORDER BY id', (match_id,)).fetchall()
    return dict(match), [dict(r) for r in events], [dict(r) for r in rounds]


def fingerprint(rows):
    return hashlib.sha256(packed(rows).encode()).hexdigest()


def facts(state):
    result = state.model_dump(mode='json', exclude={'operation','public_events','observed_at','round_seq'})
    for key in ('consistent','completeness','round_completeness','unknown_fields'):
        result.pop(key)
    result['context'].pop('source_round_key', None)
    return result


def inspect_match(rows):
    row, events, rounds = rows
    final = Snapshot.model_validate_json(row['snapshot'])
    if (row['lifecycle'] != 'ended' or row['completeness'] != 'full' or final.lifecycle != 'ended'
            or final.completeness != 'full' or not final.consistent):
        return None, 'ineligible_lifecycle_or_completeness'
    starts = [e for e in events if e['kind']=='ActionNewRound']
    if not starts or [e['seq'] for e in events] != list(range(1,len(events)+1)):
        return None, 'missing_or_noncontiguous_events'
    expected = {e['round_uid']:i for i,e in enumerate(starts,1)}
    if None in expected or len(expected)!=len(starts) or set(expected)!={r['id'] for r in rounds}:
        return None, 'round_identity_set_differs'
    changes = [{'id':r['id'],'before':r['seq'],'after':expected[r['id']]}
               for r in rounds if r['seq']!=expected[r['id']]]
    if final.round_seq==len(starts) and not changes:
        return None, 'indices_already_match'
    state = Snapshot(match_id=final.match_id,self_seat=final.self_seat,rules=final.rules)
    for raw in events:
        event = Store._event(raw)
        state = apply_event(state,event)
        state.state_version = max(state.state_version,event.seq)
        if event.kind=='ActionNewRound' and state.round_uid!=raw['round_uid']:
            return None, 'replayed_round_identity_differs'
    if facts(state)!=facts(final):
        return None, 'non_index_facts_differ'
    return {'match_id':row['id'],'fingerprint':fingerprint(rows),
            'snapshot_index':{'before':final.round_seq,'after':state.round_seq},
            'round_indices':changes}, None


def plan(database):
    db = connect(database)
    report = {'schema_version':'archive-round-index-plan.v1','created_at':utcnow(),
              'production_data_modified':False,'changes':[],'skipped':[]}
    try:
        db.execute('BEGIN')
        ids = [r[0] for r in db.execute('SELECT id FROM matches ORDER BY observed_at')]
        for match_id in ids:
            try:
                change,reason = inspect_match(material(db,match_id))
            except (ValueError,KeyError,TypeError) as error:
                change,reason = None,'replay_error_'+type(error).__name__
            if change:
                report['changes'].append(change)
            else:
                report['skipped'].append({'match_id':match_id,'reason':reason})
    finally:
        db.close()
    return report


def apply_plan(database, report):
    if report.get('schema_version')!='archive-round-index-plan.v1':
        raise ValueError('unsupported_index_plan')
    changes = report['changes']
    if len({c['match_id'] for c in changes})!=len(changes):
        raise ValueError('duplicate_planned_match')
    if not changes:
        return {'changed_matches':0,'changed_rounds':0,'backup':None}
    db = connect(database,writable=True)
    backup_path = database.with_name(database.name+'.before-round-index-'+uuid.uuid4().hex+'.bak')
    try:
        # SQLite backup includes WAL and yields a consistent, independently
        # readable copy. Recheck both copies rather than relying on timestamps.
        backup = sqlite3.connect(backup_path)
        try:
            db.backup(backup)
            backup.row_factory = sqlite3.Row
            for change in changes:
                if fingerprint(material(backup,change['match_id']))!=change['fingerprint']:
                    raise ValueError('index_plan_stale_before_backup')
        finally:
            backup.close()
        with db:
            db.execute('BEGIN IMMEDIATE')
            for proposed in changes:
                rows = material(db,proposed['match_id'])
                actual,reason = inspect_match(rows)
                if actual!=proposed:
                    raise ValueError('index_plan_stale_or_invalid_'+(reason or 'changed'))
                raw = json.loads(rows[0]['snapshot'])
                raw['round_seq'] = actual['snapshot_index']['after']
                db.execute('UPDATE matches SET snapshot=? WHERE id=?', (packed(raw),actual['match_id']))
                for change in actual['round_indices']:
                    db.execute('UPDATE rounds SET seq=? WHERE id=? AND match_id=?',
                               (change['after'],change['id'],actual['match_id']))
                db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                           (uuid.uuid4().hex,actual['match_id'],None,'archive_repair',utcnow(),
                            packed({'kind':'verified_round_index_rebuild','plan_created_at':report['created_at'],
                                    'changes':actual,'backup':backup_path.name,'original_events_modified':False})))
        return {'changed_matches':len(changes),'changed_rounds':sum(len(c['round_indices']) for c in changes),
                'backup':str(backup_path)}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('runtime/archive.sqlite'))
    parser.add_argument('--output',type=Path,default=Path('runtime/archive-round-index-plan.json'))
    parser.add_argument('--apply-plan',type=Path,help='Explicitly apply a previously inspected dry-run plan')
    args = parser.parse_args()
    if args.output.resolve()==args.database.resolve() or (args.apply_plan and args.output.resolve()==args.apply_plan.resolve()):
        parser.error('output must preserve the database and original plan')
    if args.apply_plan:
        result = apply_plan(args.database,json.loads(args.apply_plan.read_text(encoding='utf8')))
    else:
        result = plan(args.database)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('changes','skipped')},ensure_ascii=False))


if __name__=='__main__':
    main()
