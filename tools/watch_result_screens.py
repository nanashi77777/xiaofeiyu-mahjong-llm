"""Capture result screens only between action windows; no game input."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from majsoul_llm.config import Settings
from majsoul_llm.store import Store,utcnow


async def run(args):
    settings=Settings.load()
    store=Store(settings.runtime/'archive.sqlite')
    args.output.mkdir(parents=True,exist_ok=True)
    seen=set()
    started=time.monotonic()
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{settings.port}',trust_env=False,timeout=15,
        headers={'Authorization':'Bearer '+settings.control_token.get_secret_value()}) as client:
        while time.monotonic()-started < args.seconds:
            state=store.latest()
            if state and state.phase.value in ('round_end','ended') and state.round_uid not in seen:
                seen.add(state.round_uid)
                for index in range(args.frames):
                    current=store.latest(state.match_id)
                    if current.round_uid != state.round_uid or current.operation:
                        break
                    response=await client.get('/control/screenshot')
                    response.raise_for_status()
                    name=f'{state.match_id}-{state.round_seq}-{index}.png'
                    (args.output/name).write_bytes(response.content)
                    with (args.output/'index.jsonl').open('a',encoding='utf8') as file:
                        file.write(json.dumps({'at':utcnow(),'match_id':state.match_id,'round_uid':state.round_uid,
                                               'phase':current.phase.value,'file':name})+'\n')
                    print(name,flush=True)
                    await asyncio.sleep(.7)
            await asyncio.sleep(.3)
    store.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=14400)
    parser.add_argument('--frames',type=int,default=16)
    parser.add_argument('--output',type=Path,default=Path('runtime/result-observation'))
    asyncio.run(run(parser.parse_args()))
