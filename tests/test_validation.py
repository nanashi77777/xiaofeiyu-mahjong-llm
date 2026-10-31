from types import SimpleNamespace

from majsoul_llm.domain import Snapshot
from majsoul_llm.validation import match_report


def test_practice_report_pages_all_windows_and_retains_failed_denominator():
    events = [{'id':str(i),'received_at':'test','data':{'operation':{'seat':0,
               'operation_list':[{'type':1}],'time_fixed':5000,'time_add':10000}}} for i in range(501)]
    rows = {'decision':[], 'receipt':[], 'decision_failure':[]}
    for i in range(500):
        action = {'kind':'discard','window_id':str(i)+':op'}
        rows['decision'].append({'id':'d'+str(i),'purpose':'decision','created_at':'a','payload':{'action':action}})
        rows['receipt'].extend([
            {'id':'m'+str(i),'purpose':'receipt','created_at':'b','payload':{'action':action,'status':'mouse_sent','read_to_mouse_ms':4000 if i < 490 else 6000}},
            {'id':'c'+str(i),'purpose':'receipt','created_at':'c','payload':{'action':action,'status':'server_confirmed','elapsed_ms':4100}},
        ])
    rows['decision_failure'].append({'id':'f','purpose':'decision_failure','created_at':'d',
                                     'payload':{'window_id':'500:op','error_type':'TimeoutError'}})
    fake = SimpleNamespace(latest=lambda mid:Snapshot(match_id=mid,self_seat=0),
                           events=lambda mid,offset,limit:events[offset:offset+min(limit,200)],
                           records=lambda purpose,mid,limit:rows.get(purpose,[]))
    result = match_report(fake,'example')
    assert result['opportunities'] == 501 and result['server_confirmed'] == 500
    assert result['within_5s_confirmed'] == 490 and result['within_5s_rate'] == 490/501
    assert result['read_to_mouse_ms']['p99'] == 6000
    assert len(result['failures']) == 1 and result['windows']['500:op']['failures']
    assert not result['windows']['500:op']['mouse_sent']
    rows['receipt'].append({'id':'uncertain','purpose':'receipt','created_at':'e',
        'payload':{'action':{'kind':'pass','window_id':'500:op'},'status':'execution_uncertain',
                   'reason':'InputDeferred: observation superseded before press'}})
    result=match_report(fake,'example')
    assert any(f['error_type']=='execution_uncertain' and f['detail'] for f in result['failures'])
