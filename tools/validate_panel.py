"""Render the real panel in an isolated browser with intercepted local routes.

All control requests terminate in the fixture handler. The game browser and
production service are never opened or controlled by this tool.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from majsoul_llm.config import ROOT
from majsoul_llm.store import utcnow


async def run(output):
    report = {'schema_version':'panel-isolation.v1', 'started_at':utcnow(),
              'source':'production_panel_html_with_intercepted_routes',
              'production_controls':0, 'external_messages':0, 'checks':[], 'posts':[]}
    command = {'command_id':'fixture-qq-command', 'status':'waiting_user', 'command':{'kind':'login'}}
    status = {'enabled':False, 'status':'stopped', 'state':{'lifecycle':'interrupted', 'scores':[25000]*4},
              'navigation':{'active_command':None, 'recent':[command]},
              'model':'fixture-only', 'input_mode':'browser_background', 'time_bank':10}
    failure, errors = False, []

    async def handler(route):
        request, path = route.request, route.request.url.split('://',1)[1].partition('/')[2]
        if not path:
            await route.fulfill(content_type='text/html', body=(ROOT/'src/majsoul_llm/panel.html').read_text(encoding='utf8'))
        elif path=='control/status':
            if failure:
                await route.abort('connectionrefused')
            else:
                await route.fulfill(json=status)
        elif request.method=='POST':
            report['posts'].append({'path':path, 'body':request.post_data_json})
            await route.fulfill(json={'status':'cancelled'})
        elif path.startswith('api/v1/mahjong/'):
            await route.fulfill(json={'data':[], 'completeness':'partial'})
        else:
            await route.fulfill(status=404, json={'error':'fixture_route_not_found'})

    def check(name, passed):
        report['checks'].append({'name':name, 'passed':bool(passed)})

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width':1440, 'height':1100})
            page.on('pageerror', lambda error: errors.append(type(error).__name__))
            await page.route('**/*', handler)
            await page.goto('http://127.0.0.1:8765/')
            await page.locator('#token').fill('isolated-non-secret-fixture')
            await page.get_by_role('button', name='连接面板', exact=True).click()
            await page.wait_for_function("document.getElementById('badge').textContent==='stopped'")
            await page.evaluate('clearInterval(timer)')
            check('connected_state_rendered', 'interrupted' in await page.locator('#state').inner_text())
            check('last_received_time_visible', '最后收到状态' in await page.locator('#updated').inner_text())
            check('existing_qq_command_restored', await page.locator('#nav-command').input_value()==command['command_id'])
            await page.get_by_role('button', name='取消所选指令', exact=True).click()
            check('cancel_uses_existing_command_id', report['posts']==[{
                'path':'api/v1/mahjong/navigation/commands/fixture-qq-command/cancel', 'body':{}}])
            failure = True
            await page.evaluate('refresh()')
            check('disconnect_badge_reports_unknown', '当前状态未知' in await page.locator('#badge').inner_text())
            check('previous_state_marked_stale', await page.locator('#observations').get_attribute('data-stale')=='true')
            check('failure_message_preserves_last_snapshot_meaning', '不能视作实时状态' in await page.locator('#message').inner_text())
            await page.screenshot(path=str(output.with_suffix('.stale.png')), full_page=True)
            failure = False
            status.update(enabled=True, status='deciding')
            await page.evaluate('refresh()')
            check('reconnect_removes_stale_marker', await page.locator('#observations').get_attribute('data-stale') is None)
            check('reconnect_reports_current_status', await page.locator('#badge').inner_text()=='deciding')
            # A fetch that never produces headers must still release refresh.
            await page.evaluate("""() => {window.fixtureFetch=fetch;window.fetch=()=>new Promise(()=>{});window.fixtureRefresh=refresh()}""")
            await page.wait_for_function('refreshing===false', timeout=7000)
            check('hung_status_releases_refresh_lock', '当前状态未知' in await page.locator('#badge').inner_text())
            await page.evaluate('window.fetch=window.fixtureFetch')
            await page.evaluate('refresh()')
            check('status_recovers_after_timeout', await page.locator('#badge').inner_text()=='deciding')
            # Response-body stalls are bounded by the same overall deadline.
            body_result = await page.evaluate("""async() => {
                window.fetch=async()=>({ok:true,json:()=>new Promise(()=>{})});
                try{await api('/fixture/body',undefined,{timeoutMs:60});return false}
                catch(e){return e.message.includes('超时')}
                finally{window.fetch=window.fixtureFetch}
            }""")
            check('response_body_stall_is_bounded', body_result)
            post_result = await page.evaluate("""async() => {
                let calls=0;window.fetch=()=>{calls++;return new Promise(()=>{})};
                try{await api('/control/fixture',{}, {timeoutMs:60});return false}
                catch(e){return calls===1&&e.message.includes('结果未知')}
                finally{window.fetch=window.fixtureFetch}
            }""")
            check('post_timeout_reports_unknown_without_retry', post_result)
            # Reconnecting while an old read is pending must start a new read.
            await page.evaluate("""() => {
                window.fixtureOldResolve=null;let first=true;
                window.fetch=(...args)=>first?(first=false,new Promise(r=>window.fixtureOldResolve=r)):window.fixtureFetch(...args);
                window.fixtureOldRefresh=refresh();
            }""")
            await page.wait_for_function('window.fixtureOldResolve!==null')
            status['status'] = 'new-connection'
            await page.evaluate('connect()')
            await page.evaluate('clearInterval(timer)')
            await page.evaluate("""async() => {
                fixtureOldResolve({ok:true,json:async()=>({status:'obsolete-connection'})});
                await fixtureOldRefresh;window.fetch=window.fixtureFetch;
            }""")
            check('reconnect_can_replace_pending_poll', await page.locator('#badge').inner_text()=='new-connection')
            check('old_request_cannot_overwrite_new_connection', await page.locator('#badge').inner_text()!='obsolete-connection')
            # A stop request dispatches even with a pending poll.
            await page.evaluate("""() => {
                window.fixtureStopResolve=null;
                window.fetch=(path,...args)=>path==='/control/status'?new Promise(r=>window.fixtureStopResolve=r):window.fixtureFetch(path,...args);
                window.fixtureStopRefresh=refresh();
            }""")
            await page.wait_for_function('window.fixtureStopResolve!==null')
            await page.evaluate("control('stop')")
            check('stop_dispatches_independent_of_poll_lock', report['posts'][-1]['path']=='control/stop')
            await page.evaluate("""async() => {fixtureStopResolve({ok:true,json:async()=>({status:'stopped',navigation:{recent:[]}})});await fixtureStopRefresh;window.fetch=window.fixtureFetch}""")
            await page.select_option('#nav-kind', 'join_friend')
            await page.locator('#nav-code').fill('001234')
            await page.locator('#nav-ready').uncheck()
            await page.get_by_role('button', name='执行选场', exact=True).click()
            posted = report['posts'][-1]['body']
            check('room_code_keeps_leading_zeroes_and_explicit_ready', posted['room_code']=='001234' and posted['ready'] is False)
            await page.select_option('#nav-kind', 'ranked')
            check('ranked_hides_single_and_ai_lengths', await page.locator('#nav-length option[value=single]').get_attribute('hidden') is not None
                  and await page.locator('#nav-length option[value=ai]').get_attribute('hidden') is not None)
            await page.set_viewport_size({'width':740, 'height':1000})
            check('mobile_layout_has_no_horizontal_overflow', await page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            await page.screenshot(path=str(output.with_suffix('.mobile.png')), full_page=True)
            check('no_uncaught_browser_errors', not errors)
        finally:
            await browser.close()
    report['finished_at'] = utcnow()
    report['passed'] = all(c['passed'] for c in report['checks'])
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runtime/panel-validation.json'))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(asyncio.run(run(args.output)))
