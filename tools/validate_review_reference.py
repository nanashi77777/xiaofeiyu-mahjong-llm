"""Exercise real-model reference lookup through Reviewer on isolated fixtures.

The first lookup is explicitly requested by this validation, not presented as
the model's spontaneous production behavior. No game or QQ input is performed.
All memories and usage stay in a separate ignored validation database.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from majsoul_llm.config import Settings
from majsoul_llm.domain import Event, Snapshot
from majsoul_llm.model import ModelClient
from majsoul_llm.review import Reviewer
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store, utcnow
from majsoul_llm.rules import legal_actions
from majsoul_llm.wire import build_context


async def run(args):
    settings=Settings.load()
    if args.database.exists():
        raise ValueError('use a new validation database to retain prior evidence')
    store=Store(args.database)
    model=ModelClient(settings,store)
    state=Snapshot(match_id='constructed-reference-validation',self_seat=0)
    stamp=utcnow()
    for index,(kind,data) in enumerate((
        ('ActionNewRound',{'tiles':'1m 2m 3m 4m 5m 6m 1p 2p 3p 7s 8s 9s 1z 1z'.split(),
                           'scores':[25000]*4,'operation':{'operation_list':[{'type':1}]}}),
        ('ActionNoTile',{'scores':[]}),
        ('NotifyGameEndResult',{'result':{'players':[{'seat':i,'part_point_1':25000} for i in range(4)]}}),
    )):
        event=Event(id=f'constructed-reference-{index}',match_id=state.match_id,seq=index,
                    kind=kind,data=data,source='fixture',received_at=stamp,received_mono=0)
        state=apply_event(state,event)
        store.save_event(event,state)
        if index==0:
            decision_state=state.model_copy(deep=True)
    captured=[]
    class Client:
        async def request(self,payload,**kwargs):
            if not payload.get('reference_lookups'):
                kwargs['system'] += ('\n本次是只读资料接口验收。首次回复必须先用read_yaku_reference查询'
                    'tanyao、chanta、junchan；收到资料后再总结，区分副露条件，不编造策略经验。')
            output,metrics=await model.request(payload,**kwargs)
            captured.append({'had_reference_lookup':bool(payload.get('reference_lookups')),
                             'metrics':metrics})
            return output,metrics
    report={'kind':'constructed_fixture_real_channel_review_reference',
            'fixture':True,'forced_first_lookup':True,'game_inputs':0,'qq_messages':0,
            'production_archive_modified':False,'model':settings.model,'reasoning':'none'}
    try:
        async def capture_request(request):
            body=json.loads(request.content)
            payload=json.loads(body['input'][0]['content'])
            encoded=json.dumps(payload,ensure_ascii=False)
            assert 'yaku_reference' not in payload and 'catalog_version' not in encoded
            assert 'verification_status' not in encoded and 'excluded_local_yaku' not in encoded
            assert payload['current_yaku_routes'] and payload['yaku_constraints']['dora_alone_is_not_yaku']
            assert body['model']==settings.model and body['reasoning']=={'effort':'none'}
            report['realtime_request_audit']={'payload':payload,'instructions':body['instructions'],
                'text':body['text'],'contains_full_reference':False,'outbound_request_captured':True}
        model.http.event_hooks['request'].append(capture_request)
        context=build_context(decision_state,legal_actions(decision_state),compact=True)
        context.data['clock'].update(soft_seconds_remaining=5,decision_seconds_remaining=15)
        choice=await model.begin_decision(context,time.monotonic()+25)
        decision,action,metrics=await choice.completion
        report['decision']={'action_id':action.id,'reason':decision.reason,'metrics':metrics}
        model.http.event_hooks['request'].remove(capture_request)
        await Reviewer(store,Client(),lambda:False).review(state.match_id)
        lookups=store.records('review_reference_lookup',state.match_id)
        reviews=store.records('review',state.match_id)
        report.update(lookups=[r['payload'] for r in lookups],reviews=[r['payload'] for r in reviews],
                      requests=captured)
        assert lookups and reviews and any(r['had_reference_lookup'] for r in captured)
        assert all(r['payload']['result']['catalog_version']=='standard-four-player.v1' for r in lookups)
        report['passed']=True
    except Exception as error:
        report.update(passed=False,error_type=type(error).__name__,requests=captured)
    finally:
        await model.close()
        store.close()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('requests','lookups','reviews')},ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('runtime/yaku-reference-validation-v1.sqlite'))
    parser.add_argument('--output',type=Path,default=Path('runtime/yaku-reference-validation-v1.json'))
    raise SystemExit(asyncio.run(run(parser.parse_args())))
