"""Measure action-prefix and full-response latency from archived real contexts.

Calls only the selected model. This tool never opens or controls the game UI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from majsoul_llm.config import Settings
from majsoul_llm.domain import Snapshot
from majsoul_llm.model import ModelClient
from majsoul_llm.rules import legal_actions
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store,utcnow
from majsoul_llm.wire import build_context


async def run(args):
    settings=Settings.load()
    settings.stream_action_format=args.format
    store=Store(settings.runtime/'archive.sqlite')
    latest=store.latest(args.match)
    if latest is None:
        raise ValueError('archived match missing')
    state=Snapshot(match_id=latest.match_id,self_seat=latest.self_seat,rules=latest.rules)
    for row in store.db.execute('SELECT * FROM events WHERE match_id=? ORDER BY seq',(latest.match_id,)):
        event=store._event(row).model_copy(update={'historical':False})
        state=apply_event(state,event)
        if state.operation and state.operation.window_id==args.window:
            break
    else:
        raise ValueError('archived window missing')
    actions=legal_actions(state)
    model=ModelClient(settings,store)
    report={'kind':'archived_context_stream_probe','started_at':utcnow(),
            'match_id':state.match_id,'window_id':args.window,'format':args.format,'ui_inputs':0,'samples':[]}
    try:
        for _ in range(args.samples):
            context=build_context(state,actions,compact=settings.compact_validated)
            context.data['clock'].update(soft_seconds_remaining=5,decision_seconds_remaining=15)
            sample={'q':context.q,'started_at':utcnow()}
            try:
                choice=await model.begin_decision(context,time.monotonic()+15)
                sample.update(action_id=choice.action.id,action_ready_ms=choice.metrics['action_ready_ms'],
                              ttft_ms=choice.metrics['ttft_ms'])
                decision,_,metrics=await choice.completion
                sample.update(status='complete',full_ms=metrics['elapsed_ms'],usage=metrics['usage'],
                              reason=decision.reason,plan_updated=decision.plan_update is not None)
            except Exception as error:
                sample.update(status='failed',error_type=type(error).__name__)
            report['samples'].append(sample)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            brief={k:v for k,v in sample.items() if k not in ('action_id','reason','usage')}
            brief['tokens']={k:sample.get('usage',{}).get(k) for k in ('input_tokens','output_tokens','total_tokens')}
            print(json.dumps(brief,ensure_ascii=False),flush=True)
    finally:
        await model.close()
        report['finished_at']=utcnow()
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        store.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--match',required=True)
    parser.add_argument('--window',required=True)
    parser.add_argument('--samples',type=int,default=5)
    parser.add_argument('--format',choices=('nested','short'),default='nested')
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
