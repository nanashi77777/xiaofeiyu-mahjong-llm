"""Read-only input latency analysis, including observer handoff after model commit.

Execution input_ms starts after observation cancellation has drained. Report
model-commit-to-mouse separately so moving work across that boundary is visible.
Old builds without a monotonic commit timestamp receive only a labeled estimate.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

from majsoul_llm.store import utcnow


def distribution(values):
    if not values:
        return {'n':0}
    values = sorted(values)
    return {'n':len(values), 'p50_ms':statistics.median(values),
            'p95_ms':values[math.ceil(.95*len(values))-1], 'max_ms':values[-1]}


def analyze(database, matches):
    connection = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        connection.execute('BEGIN')
        frozen = {mid:[(kind,json.loads(raw)) for kind,raw in connection.execute(
            "SELECT purpose,payload FROM records WHERE match_id=? AND purpose IN "
            "('decision','receipt','decision_failure') ORDER BY created_at,id", (mid,))] for mid in matches}
    finally:
        connection.close()
    report = {'schema_version':'input-latency.v1', 'at':utcnow(), 'read_only':True,
              'same_context_paired_trial':False, 'matches':[]}
    for mid, records in frozen.items():
        decisions, receipts, failures = {}, {}, []
        for kind, payload in records:
            if kind == 'decision':
                decisions[payload['action']['id']] = payload
            elif kind == 'receipt':
                receipts[payload['id']] = payload
            else:
                failures.append(payload)
        samples = []
        for receipt in receipts.values():
            if receipt['status'] != 'server_confirmed':
                continue
            decision = decisions.get(receipt['action']['id'])
            sample = {'action_id':receipt['action']['id'], 'kind':receipt['action']['kind'],
                      'input_ms':receipt.get('input_ms'), 'read_to_mouse_ms':receipt.get('read_to_mouse_ms'),
                      'preobserved':any(s.get('stage')=='preobserved_target' for s in receipt.get('input_steps', []))}
            metrics = decision['metrics'] if decision else {}
            sample['handoff_ms'] = metrics.get('observation_handoff_ms')
            sample['handoff_mode'] = metrics.get('observation_handoff_mode')
            commit = metrics.get('action_committed_mono')
            if commit is not None and receipt.get('clicked_mono') is not None:
                sample['post_model_ms'] = (receipt['clicked_mono']-commit)*1000
                sample['timing_source'] = 'recorded_monotonic_commit_and_click'
            elif (metrics.get('purpose')=='decision' and not metrics.get('race_id')
                  and metrics.get('read_to_model_ms') is not None and metrics.get('action_ready_ms') is not None
                  and sample['read_to_mouse_ms'] is not None):
                sample['post_model_ms'] = sample['read_to_mouse_ms']-metrics['read_to_model_ms']-metrics['action_ready_ms']
                sample['timing_source'] = 'estimate_subtract_request_timing_no_repair_or_race'
            else:
                sample['post_model_ms'], sample['timing_source'] = None, 'unknown'
            samples.append(sample)
        summary = {'match_id':mid, 'model_decisions':len(decisions), 'decision_failures':len(failures),
                   'receipt_statuses':dict(Counter(r['status'] for r in receipts.values())),
                   'confirmed':len(samples), 'preobserved':sum(s['preobserved'] for s in samples),
                   'five_second_confirmed':sum(s['read_to_mouse_ms']<=5000 for s in samples if s['read_to_mouse_ms'] is not None),
                   'distributions':{key:distribution([s[key] for s in samples if s[key] is not None])
                                    for key in ('input_ms','read_to_mouse_ms','post_model_ms','handoff_ms')},
                   'handoff_modes':dict(Counter(s['handoff_mode'] for s in samples)),
                   'timing_sources':dict(Counter(s['timing_source'] for s in samples)), 'samples':samples}
        report['matches'].append(summary)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('runtime/archive.sqlite'))
    parser.add_argument('--match', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.database, args.match)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{k:v for k,v in m.items() if k!='samples'} for m in result['matches']], ensure_ascii=False))
