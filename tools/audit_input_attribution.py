"""Audit existing input evidence using a read-only consistent transaction."""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from majsoul_llm.input_origin import input_origin
from majsoul_llm.store import utcnow


def audit(database: Path):
    source = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute('BEGIN')
        records = [{**dict(r),'payload':json.loads(r['payload'])} for r in source.execute(
            "SELECT * FROM records WHERE purpose IN ('client_input','server_input_ack','receipt')")]
    finally:
        source.close()
    requests = {r['id']:r for r in records if r['purpose']=='client_input'}
    acks = {r['id']:r for r in records if r['purpose']=='server_input_ack'}
    automatic = {rid for rid,r in requests.items() if input_origin(r['payload'])=='client_automatic'}
    automatic_windows = {(requests[rid]['match_id'], requests[rid]['payload'].get('window_id')) for rid in automatic}
    errors, counts, statuses = [], Counter(), Counter()
    for ack in acks.values():
        ref = ack['payload'].get('request_record_id')
        if ref is None:
            counts['legacy_unlinked_acks'] += 1
            continue
        request = requests.get(ref)
        if (not request or request['match_id'] != ack['match_id'] or request['round_uid'] != ack['round_uid']
                or request['payload'].get('window_id') != ack['payload'].get('window_id')
                or request['payload'].get('method') != ack['payload'].get('method')):
            errors.append({'record_id':ack['id'],'reason':'request_ack_scope_mismatch'})
        else:
            counts['linked_acks'] += 1
    for receipt in (r for r in records if r['purpose']=='receipt'):
        data = receipt['payload']
        refs = data.get('client_input_ids', [])
        if not refs:
            counts['receipts_without_request_refs'] += 1
        else:
            counts['receipts_with_request_refs'] += 1
        for ref in refs:
            request = requests.get(ref)
            if (not request or request['match_id'] != receipt['match_id'] or request['round_uid'] != receipt['round_uid']
                    or request['payload'].get('window_id') != data['action']['window_id']):
                errors.append({'record_id':receipt['id'],'reason':'receipt_request_scope_mismatch'})
        ack = acks.get(data.get('actual_ack_id'))
        if data.get('actual_ack_id') and (not ack or ack['match_id'] != receipt['match_id']
                or ack['round_uid'] != receipt['round_uid']
                or ack['payload'].get('window_id') != data['action']['window_id']):
            errors.append({'record_id':receipt['id'],'reason':'receipt_ack_scope_mismatch'})
        auto = bool(automatic.intersection(refs)) or bool(ack and input_origin(ack['payload'].get('input',{}))=='client_automatic')
        # Older receipts have no direct references. Preserve that limitation,
        # and separately expose same-window automatic traffic without guessing.
        same_window = (receipt['match_id'],data['action']['window_id']) in automatic_windows
        if auto or same_window:
            statuses[data['status']] += 1
        if auto and data['status']=='server_confirmed':
            errors.append({'record_id':receipt['id'],'reason':'automatic_input_confirmed_as_bot'})
    return {'schema_version':'input-attribution-audit.v1','frozen_at':utcnow(),
            'source':'consistent_read_only_production_archive','production_data_modified':False,
            'model_requests':0,'requests':len(requests),'automatic_requests':len(automatic),'acks':len(acks),
            'reference_counts':dict(counts),'automatic_window_receipt_statuses':dict(statuses),
            'errors':errors,'passed':bool(requests) and not errors}


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('runtime/archive.sqlite'))
    parser.add_argument('--output',type=Path,default=Path('runtime/input-attribution-audit.json'))
    args = parser.parse_args()
    report = audit(args.database)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='errors'} | {'errors':len(report['errors'])}))
    raise SystemExit(0 if report['passed'] else 1)
