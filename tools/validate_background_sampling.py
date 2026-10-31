"""Compare held/released input sampling on an isolated, headless 15Hz canvas.

This controls no game and sends no protocol packets. The legacy control uses
the old two-RAF press/one-RAF release; production uses Browser.background_click.
Reports, browser event timestamps and sampled results stay in ignored runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from pathlib import Path

from playwright.async_api import async_playwright

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings


PAGE = """<canvas width=300 height=180></canvas><script>
const c=document.querySelector('canvas'), g=c.getContext('2d');
let held=false, sampled=false, x=5, y=5, accepted=0;
window.observed=[];
window.__mjLocalPointer={x:5,y:5};
for(const kind of ['mousemove','mousedown','mouseup'])addEventListener(kind,e=>{
  x=e.clientX;y=e.clientY;window.__mjLocalPointer={x,y};
  if(kind==='mousedown')held=true;
  if(kind==='mouseup')held=false;
  observed.push({kind,x,y,at:performance.now()});
});
setInterval(()=>{
  if(sampled && !held && x>=30 && x<=90 && y>=30 && y<=90)accepted++;
  sampled=held;
  g.clearRect(0,0,300,180);g.fillStyle=held?'#ff9900':'#0099cc';
  g.fillRect(30,30,60,60);g.fillText(String(accepted),130,60);
},1000/15);
window.acceptedClicks=()=>accepted;
</script>"""


async def legacy_click(page, point):
    await page.mouse.move(*point)
    await page.evaluate('() => new Promise(r=>requestAnimationFrame(r))')
    await page.mouse.down()
    await page.evaluate('() => new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    await page.mouse.up()
    await page.evaluate('() => new Promise(r=>requestAnimationFrame(r))')
    await page.mouse.move(5,5)


async def run(args):
    report = {'kind':'isolated_15hz_visible_canvas_sampling','game_input':False,
              'model_calls':0,'samples':[]}
    async with async_playwright() as playwright:
        chrome = await playwright.chromium.launch(headless=True)
        page = await chrome.new_page()
        await page.set_content(PAGE)
        bound = Browser(Settings(game_url='about:blank'),None)
        bound.page = page
        try:
            for mode in ('legacy_raf_only','production'):
                for i in range(args.samples):
                    before = await page.evaluate('acceptedClicks()')
                    await asyncio.sleep((i%7)*.011)
                    start = time.monotonic()
                    if mode == 'production':
                        await bound.background_click((60,60),start+2,threading.Event())
                    else:
                        await legacy_click(page,(60,60))
                    elapsed = (time.monotonic()-start)*1000
                    await asyncio.sleep(.075)
                    after = await page.evaluate('acceptedClicks()')
                    report['samples'].append({'mode':mode,'index':i,'accepted':after-before,
                                              'elapsed_ms':elapsed})
            report['events'] = await page.evaluate('observed')
            report['summary'] = {mode:{'trials':args.samples,'accepted':sum(
                s['accepted'] for s in report['samples'] if s['mode']==mode)}
                for mode in ('legacy_raf_only','production')}
            report['passed'] = report['summary']['production']['accepted'] == args.samples
        finally:
            await bound.release_background()
            await chrome.close()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('events','samples')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=20)
    parser.add_argument('--output',type=Path,default=Path('runtime/background-sampling-v1.json'))
    args=parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))
