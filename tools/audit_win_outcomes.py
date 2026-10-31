"""Read-only audit of real archived ron/tsumo attribution; no model requests."""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from majsoul_llm.domain import tile_index
from majsoul_llm.outcomes import win_outcomes, win_summary
from majsoul_llm.store import utcnow


def audit(database: Path, limit: int):
    source = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute('BEGIN')
        frozen = [(dict(row), [dict(e) for e in source.execute(
            'SELECT id,match_id,round_uid,seq,kind,payload FROM events WHERE match_id=? ORDER BY seq', (row['id'],))])
            for row in source.execute('SELECT * FROM matches ORDER BY observed_at DESC LIMIT ?', (limit,))]
    finally:
        source.close()
    report = {'schema_version':'win-outcome-audit.v1', 'frozen_at':utcnow(),
              'source':'consistent_read_only_production_archive', 'production_data_modified':False,
              'model_requests':0, 'matches':[], 'errors':[], 'unknowns':[]}
    totals, kinds, statuses, issues = Counter(), Counter(), Counter(), Counter()
    for row, raw_events in frozen:
        state = json.loads(row['snapshot'])
        events = [{**{k:e[k] for k in ('id','match_id','round_uid','seq','kind')},
                   'data':json.loads(e['payload'])} for e in raw_events]
        by_id = {e['id']:e for e in events}
        outcomes = win_outcomes(events)
        expected = sum(len(e['data'].get('hules', [])) for e in events if e['kind'] == 'ActionHule')
        if expected != len(outcomes) or len({w['id'] for w in outcomes}) != expected:
            report['errors'].append({'match_id':row['id'], 'reason':'win_count_or_identity_mismatch'})
        for outcome in outcomes:
            kinds[outcome['kind']] += 1
            statuses[outcome['source_status']] += 1
            issues.update(outcome['issues'])
            event = by_id[outcome['event_id']]
            index = int(outcome['id'].rsplit(':', 1)[1])
            win = event['data']['hules'][index]
            failures = []
            if outcome['winner_seat'] != win.get('seat', 0) or outcome['round_id'] != event['round_uid']:
                failures.append('winner_or_round_mismatch')
            if outcome['kind'] != ('tsumo' if win.get('zimo', False) else 'ron'):
                failures.append('win_method_mismatch')
            if win.get('hu_tile') and outcome['tile'] != win['hu_tile']:
                failures.append('winning_tile_changed')
            if outcome['source_status'] == 'confirmed':
                origin = by_id.get(outcome['source_event_id'])
                if not origin or origin['round_uid'] != event['round_uid'] or origin['seq'] >= event['seq']:
                    failures.append('source_scope_mismatch')
                else:
                    intervening = [e for e in events if origin['seq'] < e['seq'] < event['seq']]
                    if any(e['kind'] != 'ObservedPass' for e in intervening):
                        failures.append('nonadjacent_source')
                    if outcome['kind'] == 'ron':
                        seat = origin['data'].get('seat', 0)
                        if origin['kind'] not in ('ActionDiscardTile','ActionAnGangAddGang'):
                            failures.append('invalid_ron_source_kind')
                        if outcome['from_seat'] != seat or seat == outcome['winner_seat']:
                            failures.append('invalid_deal_in_seat')
                        tile = origin['data'].get('tile') if origin['kind']=='ActionDiscardTile' else origin['data'].get('tiles')
                        if outcome['tile'] and tile:
                            same = (tile_index(tile) == tile_index(outcome['tile'])
                                    if outcome['source_kind']=='ankan' else tile == outcome['tile'])
                            if not same:
                                failures.append('invalid_source_tile')
            elif outcome['from_seat'] is not None:
                failures.append('unconfirmed_deal_in_seat')
            if outcome['kind']=='tsumo' and outcome['from_seat'] is not None:
                failures.append('tsumo_mislabelled_as_deal_in')
            if failures:
                report['errors'].append({'match_id':row['id'], 'win_id':outcome['id'], 'reasons':failures})
            if outcome['source_status'] != 'confirmed' or outcome['issues']:
                report['unknowns'].append({'match_id':row['id'], **outcome})
        summary = win_summary(outcomes, state.get('self_seat', 0))
        totals.update({k:v for k,v in summary.items() if k != 'self_seat'})
        report['matches'].append({'match_id':row['id'], 'lifecycle':row['lifecycle'],
            'completeness':row['completeness'], 'event_count':len(events), 'win_summary':summary,
            'final_scores':state.get('final_scores'), 'final_ranks':state.get('final_ranks'), 'wins':outcomes})
    report.update(win_kinds=dict(kinds), source_statuses=dict(statuses), issues=dict(issues), self_totals=dict(totals),
                  passed=bool(frozen) and not report['errors'], fully_attributed=not report['unknowns'])
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('runtime/archive.sqlite'))
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--output', type=Path, default=Path('runtime/win-outcome-audit.json'))
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('limit must be positive')
    result = audit(args.database, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output':str(args.output), 'passed':result['passed'], 'matches':len(result['matches']),
                      'win_kinds':result['win_kinds'], 'source_statuses':result['source_statuses'],
                      'unknowns':len(result['unknowns']), 'errors':len(result['errors'])}))
    raise SystemExit(0 if result['passed'] else 1)
