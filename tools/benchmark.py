"""Actual channel measurements on labeled synthetic contexts; no mouse input."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from majsoul_llm.config import Settings
from majsoul_llm.domain import Event, Snapshot
from majsoul_llm.model import ModelClient
from majsoul_llm.rules import legal_actions
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store, utcnow
from majsoul_llm.wire import build_context, semantic_actions


def sample(index):
    hands=["1m 2m 3m 4m 0m 6m 2p 3p 4p 6s 7s 8s 1z 1z",
           "1m 4m 5m 6m 2p 3p 4p 6s 7s 8s 1z 1z 5z 5z",
           "2m 3m 4m 6m 7m 8m 2p 3p 4p 2s 4s 5s 6s 8s",
           "1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z 7z"]
    state=Snapshot(match_id="synthetic-benchmark",self_seat=0)
    state=apply_event(state,Event(id=f"fixture-round-{index//20}",match_id=state.match_id,seq=1,kind="ActionNewRound",
                                 received_at=utcnow(),received_mono=time.monotonic(),source="fixture",
                                 data={"tiles":hands[index%4].split(),"chang":index%2,"ju":index%4,"ben":index%3,
                                       "scores":[38000,27000,20000,15000],"doras":["9p"],"left_tile_count":45,
                                       "operation":{"seat":0,"time_fixed":5000,"time_add":10000,"operation_list":[{"type":1,"combination":[]}]}}))
    if index%4==1:
        state.players[2].riichi="established"
    return state


async def run(args):
    settings=Settings.load()
    settings.json_schema=args.schema
    store=Store(settings.runtime/"archive.sqlite")
    client=ModelClient(settings,store)
    results=[]
    try:
        for index in range(args.count):
            state=sample(index//2)
            start=time.monotonic()
            actions=legal_actions(state)
            rules_ms=(time.monotonic()-start)*1000
            rich=build_context(state,actions)
            compact=build_context(state,actions,compact=True)
            assert semantic_actions(rich)==semantic_actions(compact)
            context=compact if index%2 else rich
            context.data["clock"]["decision_seconds_remaining"]=4.2
            record={"index":index,"pair":index//2,"format":context.data["v"],"sample_source":"synthetic",
                    "rules_ms":rules_ms,"physical_input_ms":None,"status":"failed"}
            try:
                output,metrics=await client.request(context.data,purpose="decision")
                decision,action=context.validate(output)
                record.update(status="valid",action=action.kind,reason=decision.reason,metrics=metrics)
            except Exception as error:
                record.update(error_type=type(error).__name__)
            record["total_without_click_ms"]=(time.monotonic()-start)*1000
            results.append(record)
            print(json.dumps(record,ensure_ascii=False),flush=True)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({"schema":"benchmark.v1","model":settings.model,"reasoning":"none","json_schema":args.schema,"results":results},ensure_ascii=False,indent=2),encoding="utf-8",newline="\n")
    finally:
        await client.close()
        store.close()
    valid=[r for r in results if r["status"]=="valid"]
    print(json.dumps({"attempts":len(results),"valid":len(valid),"within_5s":sum(r["total_without_click_ms"]<5000 for r in valid),
                      "median_ms":statistics.median(r["total_without_click_ms"] for r in valid) if valid else None,
                      "acceptance_pass":False,"note":"Synthetic contexts; no real input/confirmation. Never substitutes VAL-005/006."},ensure_ascii=False))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count",type=int,default=16)
    parser.add_argument("--schema",action="store_true")
    parser.add_argument("--output",type=Path,default=Path("runtime/benchmark.json"))
    asyncio.run(run(parser.parse_args()))
