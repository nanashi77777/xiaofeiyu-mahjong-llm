"""One live decision; optional physical click, with independent actual-event receipt."""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from playwright.async_api import async_playwright

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings
from majsoul_llm.execution import Executor
from majsoul_llm.model import ModelClient
from majsoul_llm.rules import legal_actions
from majsoul_llm.store import Store
from majsoul_llm.wire import build_context


async def run(args):
    settings=Settings.load()
    settings.json_schema=True
    if args.background:
        settings.input_mode='browser_background'
    db=Store(settings.runtime/'archive.sqlite')
    state=db.latest()
    if not state or not state.operation or not state.consistent:
        print({'status':'waiting_for_valid_operation'})
        return
    op=state.operation
    if op.fixed_ms != 300000 or op.add_ms != 0:
        raise ValueError('This tool is only for the explicitly authorized 300+0 integration run')
    deadline=op.received_mono+300-1
    if time.monotonic()+30>=deadline:
        print({'status':'too_late_for_integration_probe'})
        return
    async with async_playwright() as playwright:
        chrome=await playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{settings.debug_port}')
        browser=Browser(settings,None)
        browser.page=chrome.contexts[0].pages[0]
        executor=Executor(browser,lambda:db.latest(state.match_id))
        windows=executor.mouse.windows()
        if args.hwnd is not None:
            windows=[w for w in windows if w.hwnd == args.hwnd]
        if len(windows)!=1:
            print({'status':'game_window_not_unique'})
            return
        await executor.bind(windows[0].hwnd)
        try:
            if args.background:
                await browser.page.mouse.move(5,5)
                await asyncio.sleep(.08)
            targets=executor.locator.hand(await browser.image_bytes(),state)
            print(json.dumps({'status':'hand_verified','targets':len(targets),'state_version':state.state_version}),flush=True)
        except Exception as error:
            print({'status':'locator_deferred','error_type':type(error).__name__,'detail':str(error)})
            return
        client=ModelClient(settings,db)
        try:
            plans=db.records('round_plan',state.match_id,100)
            plan=next((r['payload'] for r in plans if r['round_uid']==state.round_uid),None)
            context=build_context(state,legal_actions(state),plan=plan)
            context.data['clock']['decision_seconds_remaining']=4.2
            raw,metrics=await client.request(context.data,deadline=deadline)
            db.record('integration_model_output',{'raw':raw,'metrics':metrics,'profile':'300+0'},state.match_id,state.round_uid)
            decision,action=context.validate(raw)
            if decision.plan_update:
                db.record('round_plan',{**decision.plan_update.model_dump(),'version':(plan or {}).get('version',0)+1,
                                        'round_uid':state.round_uid,'state_version':state.state_version},state.match_id,state.round_uid)
            print(json.dumps({'decision':decision.model_dump(),'action':action.kind,
                              'tile':state.hand[action.tile_index] if action.tile_index is not None else None,
                              'elapsed_ms':metrics['elapsed_ms']},ensure_ascii=False),flush=True)
            db.record('decision',{'decision':decision.model_dump(),'action':action.model_dump(),'metrics':metrics,
                                  'shadow':not args.execute,'test_profile':'300+0'},state.match_id,state.round_uid)
            if not args.execute:
                return
            original=executor.mouse.cursor()
            original_foreground=executor.mouse.user.GetForegroundWindow()
            minimized=bool(executor.mouse.user.IsIconic(executor.window.hwnd))
            if args.hover_frame and action.kind=='discard':
                target=await executor.target('tile',state,[action.tile_index])
                executor.mouse.check(executor.window,target.rect,target.point,target.observed,deadline)
                executor.mouse.user.SetCursorPos(*target.point)
                await asyncio.sleep(.075)
                executor._current(state,action)
                clicked=await asyncio.to_thread(executor.mouse.click,executor.window,target.rect,target.point,target.observed,deadline)
                if executor.mouse.cursor()==target.point:
                    executor.mouse.user.SetCursorPos(*original)
            else:
                clicked=await executor.execute(state,action,deadline)
            restored=executor.mouse.cursor()==original
            db.record('integration_click',{'action':action.model_dump(),'clicked_mono':clicked,'cursor_restored':restored,
                                           'input_mode':executor.input_mode,'minimized':minimized,
                                           'foreground_unchanged':executor.mouse.user.GetForegroundWindow()==original_foreground,
                                           'profile':'300+0'},state.match_id,state.round_uid)
            print({'status':'mouse_sent','cursor_restored':restored},flush=True)
            for _ in range(40):
                await asyncio.sleep(.1)
                events=[e for e in db.events(state.match_id,offset=max(0,state.state_version-1)) if e['seq']>state.state_version]
                own=[e for e in events if e['kind']=='ActionDiscardTile' and e['data'].get('seat',0)==state.self_seat]
                if own:
                    actual=own[0]
                    correct=action.kind in ('discard','riichi') and actual['data']['tile']==state.hand[action.tile_index]
                    db.record('integration_confirmation',{'status':'server_confirmed' if correct else 'mismatch',
                                                          'actual_event_id':actual['id'],'actual_tile':actual['data']['tile'],
                                                          'intended_action':action.model_dump(),'cursor_restored':restored,
                                                          'input_mode':executor.input_mode,'minimized':minimized,
                                                          'foreground_unchanged':executor.mouse.user.GetForegroundWindow()==original_foreground,
                                                          'profile':'300+0'},state.match_id,state.round_uid)
                    print(json.dumps({'status':'server_confirmed' if correct else 'mismatch','actual_tile':actual['data']['tile']},ensure_ascii=False))
                    break
                accepted=[r for r in db.records('server_input_ack',state.match_id,20)
                          if r['payload'].get('window_id')==action.window_id
                          and r['payload'].get('accepted') and r['payload'].get('input',{}).get('cancel_operation')]
                if action.kind=='pass' and accepted:
                    db.record('integration_confirmation',{'status':'server_confirmed','actual_ack_id':accepted[0]['id'],
                              'intended_action':action.model_dump(),'cursor_restored':restored,
                              'input_mode':executor.input_mode,'minimized':minimized,
                              'foreground_unchanged':executor.mouse.user.GetForegroundWindow()==original_foreground,
                              'profile':'300+0'},state.match_id,state.round_uid)
                    print({'status':'server_confirmed','action':'pass'})
                    break
            else:
                print({'status':'no_actual_confirmation_yet'})
        except Exception as error:
            detail=error.errors(include_input=False) if hasattr(error,'errors') else str(error) if type(error).__name__=='InputDeferred' else None
            db.record('integration_deferred',{'error_type':type(error).__name__,'detail':detail},state.match_id,state.round_uid)
            print(json.dumps({'status':'deferred','error_type':type(error).__name__,'detail':detail},ensure_ascii=False,default=str))
        finally:
            await client.close()
    db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--hover-frame',action='store_true',help='Diagnostic same-target hover before immediate press')
    parser.add_argument('--background',action='store_true',help='User-authorized browser mouse simulation without system pointer')
    parser.add_argument('--hwnd',type=int,help='Explicit dedicated browser window when multiple game windows exist')
    asyncio.run(run(parser.parse_args()))
