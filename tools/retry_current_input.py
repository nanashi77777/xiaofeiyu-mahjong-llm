"""At most one same-action retry, only with current model intent and no client input."""
import asyncio
import json

from playwright.async_api import async_playwright

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings
from majsoul_llm.domain import LegalAction
from majsoul_llm.execution import Executor
from majsoul_llm.store import Store


async def main():
    settings=Settings.load()
    settings.input_mode='browser_background'
    db=Store(settings.runtime/'archive.sqlite')
    state=db.latest()
    if not state or not state.operation:
        print({'status':'no_current_operation'})
        return
    decision=next((r for r in db.records('decision',state.match_id,50) if r['payload']['action']['window_id']==state.operation.window_id),None)
    if not decision:
        print({'status':'no_current_model_action'})
        return
    action=LegalAction.model_validate(decision['payload']['action'])
    prior=db.records('integration_retry',state.match_id,100)
    if any(r['payload']['window_id']==action.window_id for r in prior):
        print({'status':'same_action_retry_already_consumed'})
        return
    db.record('integration_retry',{'window_id':action.window_id,'action_id':action.id},state.match_id,state.round_uid)
    async with async_playwright() as p:
        chrome=await p.chromium.connect_over_cdp(f'http://127.0.0.1:{settings.debug_port}')
        browser=Browser(settings,None)
        browser.page=chrome.contexts[0].pages[0]
        executor=Executor(browser,lambda:db.latest(state.match_id))
        windows=executor.mouse.windows()
        if len(windows)!=1:
            return
        await executor.bind(windows[0].hwnd)
        original=executor.mouse.cursor()
        foreground=executor.mouse.user.GetForegroundWindow()
        try:
            await executor.execute(state,action,state.operation.received_mono+299)
            print({'status':'background_same_action_sent','cursor_unchanged':executor.mouse.cursor()==original,
                   'foreground_unchanged':executor.mouse.user.GetForegroundWindow()==foreground},flush=True)
            for _ in range(30):
                await asyncio.sleep(.1)
                events=db.events(state.match_id,offset=max(0,state.state_version-1))
                own=[e for e in events if e['seq']>state.state_version and e['kind']=='ActionDiscardTile' and e['data'].get('seat',0)==state.self_seat]
                if own:
                    correct=action.kind in ('discard','riichi') and own[0]['data']['tile']==state.hand[action.tile_index]
                    report={'status':'server_confirmed' if correct else 'mismatch','actual_tile':own[0]['data']['tile'],
                            'input_mode':'browser_background','actual_event_id':own[0]['id'],'cursor_unchanged':executor.mouse.cursor()==original,
                            'profile':'300+0'}
                    db.record('integration_confirmation',report,state.match_id,state.round_uid)
                    print(json.dumps(report,ensure_ascii=False))
                    break
            else:
                print({'status':'no_actual_confirmation'})
        except Exception as e:
            print({'status':'deferred','type':type(e).__name__,'detail':str(e)})
    db.close()


if __name__=='__main__':
    asyncio.run(main())
