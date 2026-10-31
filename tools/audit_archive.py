"""Replay a read-only consistent archive snapshot and compare factual caches.

This does not change cached states, mark matches interrupted, or execute model
requests. Older-build differences are reported as findings, never repaired.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from majsoul_llm.domain import Event, Snapshot
from majsoul_llm.state import apply_event
from majsoul_llm.store import utcnow


def facts(state):
    result = state.model_dump(mode='json', exclude={'operation','public_events','observed_at'})
    # These belong to capture health rather than Mahjong event reduction.
    for key in ('consistent','completeness','round_completeness','unknown_fields'):
        result.pop(key)
    result['context'].pop('source_round_key', None)
    return result


def audit(database, limit):
    source = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute('BEGIN')
        frozen = []
        for row in source.execute('SELECT * FROM matches ORDER BY observed_at DESC LIMIT ?', (limit,)):
            events = [dict(e) for e in source.execute('SELECT * FROM events WHERE match_id=? ORDER BY seq', (row['id'],))]
            frozen.append((dict(row), events))
    finally:
        source.close()
    report = {'schema_version':'archive-replay-audit.v1', 'frozen_at':utcnow(),
              'source':'consistent_read_only_production_archive', 'production_data_modified':False,
              'rule_seed':'recorded match self_seat and rules; no final tiles or scores used as seed',
              'excluded_transport_fields':['operation','public_events','observed_at','consistent',
                                           'completeness','round_completeness','unknown_fields','context.source_round_key'],
              'matches':[], 'event_counts':{}, 'errors':[]}
    counts = Counter()
    for row, events in frozen:
        final = Snapshot.model_validate_json(row['snapshot'])
        entry = {'match_id':final.match_id, 'lifecycle':final.lifecycle, 'completeness':final.completeness,
                 'events':len(events), 'cached_version':final.state_version, 'differences':[]}
        report['matches'].append(entry)
        if not events:
            entry['status'] = 'not_replayable_no_events'
            continue
        state = Snapshot(match_id=final.match_id, self_seat=final.self_seat, rules=final.rules)
        try:
            previous_seq = 0
            for raw in events:
                counts[raw['kind']] += 1
                meta = json.loads(raw['meta']) if raw['meta'] else {}
                event = Event(id=raw['id'], match_id=raw['match_id'], seq=raw['seq'], kind=raw['kind'],
                              data=json.loads(raw['payload']), received_at=raw['received_at'],
                              received_mono=0, historical=True, source_step=meta.get('step'),
                              source=meta.get('source','websocket'))
                if event.seq != previous_seq+1:
                    entry.setdefault('sequence_gaps', []).append([previous_seq,event.seq])
                state = apply_event(state, event)
                state.state_version = max(state.state_version, event.seq)
                previous_seq = event.seq
            actual, expected = facts(state), facts(final)
            if final.lifecycle=='interrupted' and state.lifecycle!='interrupted':
                # A stopped/disconnected capture invalidates live controls
                # without inventing a server Mahjong event.
                entry['capture_interruption_excluded'] = ['lifecycle','phase']
                for key in entry['capture_interruption_excluded']:
                    actual.pop(key)
                    expected.pop(key)
            for key in expected:
                if actual[key] != expected[key]:
                    entry['differences'].append({'field':key, 'replayed':actual[key], 'cached':expected[key]})
            entry['status'] = 'matches' if not entry['differences'] and not entry.get('sequence_gaps') else 'differs'
        except Exception as error:
            entry.update(status='replay_failed', error_type=type(error).__name__, last_replayed_seq=state.state_version)
        if entry['status'] in ('differs','replay_failed'):
            report['errors'].append({'match_id':final.match_id, 'status':entry['status'],
                                     'fields':[d['field'] for d in entry['differences']]})
    report['event_counts'] = dict(counts)
    report['passed'] = bool(frozen) and not report['errors'] and all(e['status']=='matches' for e in report['matches'])
    return report


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('runtime/archive.sqlite'))
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--output', type=Path, default=Path('runtime/archive-replay-audit.json'))
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('limit must be positive')
    result = audit(args.database, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='matches'}, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
