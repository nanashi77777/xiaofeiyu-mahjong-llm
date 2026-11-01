"""Evidence-derived practice reports; failure windows remain in denominators."""
from __future__ import annotations

import math

from .input_origin import input_origin


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered)*fraction)-1)]


def match_report(store, match_id):
    state = store.latest(match_id)
    if state is None:
        raise ValueError('match not recorded')
    events = []
    while batch := store.events(match_id, offset=len(events), limit=200):
        events.extend(batch)
    windows = {}
    for event in events:
        op = event['data'].get('operation') or {}
        if op.get('operation_list') and op.get('seat', state.self_seat) == state.self_seat:
            windows[event['id']+':op'] = {
                'event_id':event['id'], 'received_at':event['received_at'],
                'fixed_ms':op.get('time_fixed'), 'add_ms':op.get('time_add'),
                'decision_count':0, 'failures':[], 'mouse_sent':False, 'server_confirmed':False,
            }
    failures, mismatches, cancelled = [], [], []
    records = [row for purpose in ('decision','decision_failure','decision_cancelled','receipt')
               for row in store.records(purpose, match_id, limit=100000)]
    for row in sorted(records, key=lambda r:r['created_at']):
        data, purpose = row['payload'], row['purpose']
        window_id = data.get('window_id') or data.get('action', {}).get('window_id')
        window = windows.get(window_id)
        if purpose == 'decision' and window is not None:
            window['decision_count'] += 1
            window['last_kind'] = data['action']['kind']
            window['shadow'] = data.get('shadow', False)
        elif purpose == 'decision_failure':
            failures.append({'record_id':row['id'], **data})
            if window is not None:
                window['failures'].append(data)
        elif purpose == 'decision_cancelled':
            cancelled.append(data)
        elif purpose == 'receipt':
            if data['status'] == 'execution_uncertain':
                failure={'record_id':row['id'],'window_id':window_id,'error_type':'execution_uncertain',
                         'detail':data.get('reason')}
                failures.append(failure)
                if window is not None:
                    window['failures'].append(failure)
            if data['status'] == 'mismatch':
                mismatches.append({'record_id':row['id'], **data})
            if window is not None:
                if data['status'] == 'mouse_sent':
                    window['mouse_sent'] = True
                    window['read_to_mouse_ms'] = data.get('read_to_mouse_ms')
                elif data['status'] == 'server_confirmed':
                    window['server_confirmed'] = True
                    window['confirmation_ms'] = data.get('elapsed_ms')
    automatic_inputs = []
    for row in store.records('client_input',match_id,limit=100000):
        data = row['payload']
        window_id = data.get('window_id')
        if window_id in windows:
            windows[window_id].setdefault('client_input_refs', []).append({
                'request_id':row['id'], 'origin':input_origin(data)})
        if input_origin(data) != 'client_automatic':
            continue
        automatic_inputs.append({'record_id':row['id'], 'window_id':window_id,
                                 'cancel_operation':data.get('cancel_operation'), 'type':data.get('type')})
        if window_id in windows:
            window = windows[window_id]
            window.setdefault('automatic_input_ids', []).append(row['id'])
            if window['server_confirmed']:
                window['recorded_server_confirmed'] = True
                window['server_confirmed'] = False
                window['confirmation_excluded_reason'] = 'client_automatic_input_observed'
    for row in store.records('server_input_ack',match_id,limit=100000):
        data = row['payload']
        if data.get('window_id') in windows:
            windows[data['window_id']].setdefault('input_ack_refs', []).append({
                'ack_id':row['id'], 'request_id':data.get('request_record_id'), 'accepted':data.get('accepted')})
    durations = [w['read_to_mouse_ms'] for w in windows.values()
                 if w['mouse_sent'] and w.get('read_to_mouse_ms') is not None]
    confirmed = [w for w in windows.values() if w['server_confirmed']]
    within5 = sum(w.get('read_to_mouse_ms', float('inf')) <= 5000 for w in confirmed)
    annotations = store.records('decision_annotation',match_id,100000)
    return {
        'schema_version':'match-report.v2-input-origin',
        'match_id':match_id, 'lifecycle':state.lifecycle, 'completeness':state.completeness,
        'consistent':state.consistent, 'rounds':state.round_seq,
        'self_seat':state.self_seat, 'final_scores':state.final_scores, 'final_ranks':state.final_ranks,
        'opportunities':len(windows), 'with_model_response':sum(w['decision_count'] > 0 for w in windows.values()),
        'mouse_sent':sum(w['mouse_sent'] for w in windows.values()), 'server_confirmed':len(confirmed),
        'within_5s_confirmed':within5, 'within_5s_rate':within5/len(windows) if windows else None,
        'over_5s_measured':sum(v > 5000 for v in durations),
        'read_to_mouse_ms':{'p50':percentile(durations,.5), 'p95':percentile(durations,.95),
                            'p99':percentile(durations,.99), 'max':max(durations,default=None)},
        'failures':failures, 'mismatches':mismatches, 'cancellations':cancelled,
        'client_automatic_inputs':automatic_inputs,
        'annotations':{'complete':sum(r['payload']['status']=='complete' for r in annotations),
                       'failed':sum(r['payload']['status']=='failed' for r in annotations),
                       'cancelled':sum(r['payload']['status']=='cancelled' for r in annotations)},
        'windows':windows,
        'result_evidence':[{'record_id':r['id'], **r['payload']}
                           for r in store.records('result_ui', match_id, 100)],
        'client_interventions':[{'record_id':r['id'], **r['payload']}
                                for r in store.records('client_away', match_id, 10000)],
        'all_failures_retained':True,
    }
