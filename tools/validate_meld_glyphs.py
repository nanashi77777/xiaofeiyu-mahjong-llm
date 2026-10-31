"""Construct menus from private real glyphs; use the production classifier.

These are explicitly constructed layouts, not natural game occurrences. The
isolated browser receives only constructed images and test mouse events.
Production calibration and the real game page are never modified.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from playwright.async_api import async_playwright

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings
from majsoul_llm.locator import ScreenLocator, decode_image
from majsoul_llm.native import InputDeferred
from majsoul_llm.store import utcnow


async def validate(output):
    settings = Settings.load()
    locator = ScreenLocator(settings.runtime)
    profile = locator.profile()
    before = hashlib.sha256(locator.path.read_bytes()).hexdigest()
    glyphs, sources = {}, {}
    for tile in ('2m','3m','4m','5m','0m','6m','7m'):
        path = locator.folder/profile['templates']['tile_'+tile]['file']
        patch = decode_image(path.read_bytes())
        if patch.shape[:2] != (78,48):
            raise ValueError('this constructed layout requires 48x78 calibrated glyphs')
        face = np.full((90,60,3),235,dtype=np.uint8)
        face[6:84,6:54] = patch
        glyphs[tile] = face
        sources[tile] = {'file':path.name, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    cases = []
    report = {'schema_version':'meld-glyph-validation.v1','at':utcnow(),
              'kind':'constructed_layout_from_real_private_glyphs', 'classifier':'production_onnx',
              'natural_game_occurrences':0, 'production_inputs':0, 'browser_geometry_changes':0,
              'sources':sources, 'cases':cases}
    async with async_playwright() as pw:
        chrome = await pw.chromium.launch(channel='chrome',headless=True)
        try:
            context = await chrome.new_context(viewport={'width':profile['width'],'height':profile['height']},
                                                device_scale_factor=1)
            page = await context.new_page()
            browser = Browser(settings.model_copy(update={'game_url':'about:blank'}), None)
            browser.page = page
            browser.context = context
            for name,choices in (
                ('three_chi',[['3m','4m'],['4m','6m'],['6m','7m']]),
                ('five_red_variants',[['2m','3m'],['3m','5m'],['3m','0m'],['5m','6m'],['0m','6m']])):
                for wrapped in (False,True):
                    # UI order is independent of the offered action order.
                    displayed = list(reversed(choices))
                    canvas = np.full((profile['height'],profile['width'],3),25,dtype=np.uint8)
                    rectangles = []
                    for index,tiles in enumerate(displayed):
                        row,col = divmod(index,3) if wrapped else (0,index)
                        x,y = 100+col*210, 470-row*105
                        for offset,tile in enumerate(tiles):
                            left = x+offset*62
                            canvas[y:y+90,left:left+60] = glyphs[tile]
                        rectangles.append({'x':x,'y':y,'w':122,'h':90,'tiles':tiles})
                    raw = cv2.imencode('.png',canvas)[1].tobytes()
                    artifact = output.with_name(output.stem+'-'+name+('-wrapped' if wrapped else '-row')+'.png')
                    artifact.write_bytes(raw)
                    groups = locator.menu_groups(raw)
                    entry = {'name':name,'wrapped':wrapped, 'screenshot':artifact.name,
                             'offered':choices,'displayed':displayed,
                             'classified':[{'tiles':tiles,'confidence':box.confidence} for tiles,box in groups],
                             'clicks':[], 'rejections':[]}
                    cases.append(entry)
                    assert len(groups)==len(choices)
                    await page.set_content('<style>html,body{margin:0}</style><canvas id="unity-canvas"></canvas>')
                    await page.evaluate('''async ({image,width,height,groups}) => {
                      const c=document.querySelector('canvas');c.width=width;c.height=height;
                      const im=new Image();im.src=image;await im.decode();c.getContext('2d').drawImage(im,0,0);
                      window.testHits=[];
                      c.addEventListener('click',e=>{
                        const g=groups.filter(g=>e.clientX>=g.x&&e.clientX<g.x+g.w&&e.clientY>=g.y&&e.clientY<g.y+g.h);
                        window.testHits.push(g.length===1?g[0].tiles:null);
                      });
                    }''', {'image':'data:image/png;base64,'+base64.b64encode(raw).decode(),
                           'width':profile['width'],'height':profile['height'],'groups':rectangles})
                    for selected in choices:
                        target = locator.combo(raw,'chi',selected,choices)
                        await browser.background_click(target.center,time.monotonic()+3,threading.Event())
                        actual = await page.evaluate('window.testHits.at(-1)')
                        assert actual == selected
                        entry['clicks'].append({'selected':selected,'observed':actual,'center':target.center})
                    for invalid,offered in (
                        ('missing_group',choices[:-1]), ('extra_group',choices+[['7m','8m']]),
                        ('wrong_red',[['3m','5m'] if tiles==['3m','0m'] else tiles for tiles in choices])
                        if name=='five_red_variants' else ('wrong_red',[['3m','0m'],*choices[1:]])):
                        try:
                            locator.combo(raw,'chi',choices[0],offered)
                        except InputDeferred:
                            entry['rejections'].append(invalid)
                        else:
                            raise AssertionError('invalid menu accepted: '+invalid)
                    entry['passed'] = True
            report['calibration_unchanged'] = before == hashlib.sha256(locator.path.read_bytes()).hexdigest()
            report['passed'] = bool(cases) and all(c.get('passed') for c in cases) and report['calibration_unchanged']
        except Exception as error:
            report.update(passed=False,error_type=type(error).__name__)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            raise
        finally:
            await chrome.close()
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('runtime/meld-glyph-validation.json'))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    result = asyncio.run(validate(args.output))
    print(json.dumps({'passed':result['passed'],'cases':len(result['cases']),
                      'clicks':sum(len(c['clicks']) for c in result['cases']),
                      'production_inputs':result['production_inputs']}))
