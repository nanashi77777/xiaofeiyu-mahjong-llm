"""Actual model/input practice runner beside a stopped passive capture service.

Keeps real events in the capture service authoritative, and includes failed
opportunities in its private run report. This is a development acceptance tool.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from playwright.async_api import async_playwright

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.domain import Event
from majsoul_llm.execution import Executor
from majsoul_llm.model import ModelClient
from majsoul_llm.store import Store, utcnow


async def run(args):
    settings = Settings.load()
    settings.integration_test = True
    settings.input_mode = "browser_background"
    store = Store(settings.runtime / "archive.sqlite")
    client = ModelClient(settings, store)
    engine = SimpleNamespace(store=store, state=store.latest())
    started = time.monotonic()
    report = {"started_at":utcnow(), "profile":"300+0 development", "source":"live_websocket",
              "status":"running", "windows":[], "all_failures_retained":True}
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{settings.port}", trust_env=False,
                                 headers={"Authorization":"Bearer "+settings.control_token.get_secret_value()}) as http:
        initial = (await http.get('/control/status')).json()
        if initial['enabled'] or initial['navigation']['active_command']:
            raise ValueError('passive capture service must be stopped and navigation idle')
        async with async_playwright() as p:
            chrome = await p.chromium.connect_over_cdp(f'http://127.0.0.1:{settings.debug_port}')
            browser = Browser(settings, None)
            browser.page = chrome.contexts[0].pages[0]
            browser.cdp = await browser.page.context.new_cdp_session(browser.page)
            executor = Executor(browser, lambda:engine.state)
            await executor.bind(args.hwnd)
            coord = Coordinator(settings, engine, client, executor)
            cursor, foreground = executor.mouse.cursor(), executor.mouse.user.GetForegroundWindow()
            last_version = engine.state.state_version if engine.state else None
            last_poll = 0
            try:
                if engine.state and engine.state.operation:
                    report['windows'].append({'id':engine.state.operation.window_id,'state_version':last_version,
                                              'received_at':engine.state.operation.received_at})
                await coord.start(shadow=False)
                while time.monotonic()-started < args.seconds:
                    state = store.latest(engine.state.match_id if engine.state else None)
                    if state and state.state_version != last_version:
                        last_version = state.state_version
                        old_version = engine.state.state_version if engine.state else 0
                        engine.state = state
                        rows = [r for r in store.events(state.match_id, offset=max(0,old_version-1))
                                if r['seq'] > old_version]
                        for row in rows:
                            age=(datetime.now(timezone.utc)-datetime.fromisoformat(row['received_at'])).total_seconds()
                            event=Event.model_validate({**row,'received_mono':time.monotonic()-max(0,age)})
                            if coord.pending:
                                coord.confirm(state,event)
                        await coord.changed(state, None)
                        if state.operation:
                            window = {"id":state.operation.window_id, "state_version":state.state_version,
                                      "received_at":state.operation.received_at}
                            if not any(w['id'] == window['id'] for w in report['windows']):
                                report['windows'].append(window)
                            print(json.dumps({'opportunity':len(report['windows']),'phase':state.phase,
                                              'status':coord.status}),flush=True)
                        if state.lifecycle == 'ended':
                            report.update(status='ended',final_scores=state.final_scores,final_ranks=state.final_ranks)
                            break
                    # Pass is acknowledged by the service's observed RPC record,
                    # since it does not produce a separate action event.
                    if coord.pending:
                        window_id=coord.pending['receipt']['action']['window_id']
                        ack=next((r for r in store.records('server_input_ack',state.match_id,5)
                                  if r['payload'].get('window_id')==window_id and r['payload'].get('accepted')),None)
                        if ack:
                            await coord.input_observed('accepted',window_id,ack['payload'].get('input',{}),ack['id'])
                    if time.monotonic()-last_poll > 1:
                        last_poll=time.monotonic()
                        control=(await http.get('/control/status')).json()
                        if control['enabled'] or control['navigation']['active_command']:
                            report['status']='stopped_conflicting_control'
                            break
                    failures=store.records('decision_failure',state.match_id,1) if state else []
                    if failures and failures[0]['created_at'] >= report['started_at']:
                        report.update(status='deferred',failure=failures[0]['payload'])
                        print(json.dumps(report['failure']),flush=True)
                        break
                    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    await asyncio.sleep(.03)
            finally:
                await coord.stop('practice_runner_stopped')
                report.update(elapsed_seconds=time.monotonic()-started,cursor_unchanged=executor.mouse.cursor()==cursor,
                              foreground_unchanged=executor.mouse.user.GetForegroundWindow()==foreground)
                args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                await browser.release_background()
                await browser.cdp.detach()
                await chrome.close()
                await client.close()
                store.close()
                print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hwnd',type=int,required=True)
    parser.add_argument('--seconds',type=float,default=120)
    parser.add_argument('--output',type=Path,default=Path('runtime/practice-run.json'))
    asyncio.run(run(parser.parse_args()))
