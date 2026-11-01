"""Grey forbidden slots remain part of a complete, independently checked row."""
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from majsoul_llm.execution import Executor
from majsoul_llm.locator import ScreenLocator
from majsoul_llm.native import InputDeferred


def fixture_row(tmp_path, state):
    locator = ScreenLocator(tmp_path)
    locator.folder.mkdir()
    tiles = ['1m','0m','5m','7m','8m','3z','5z']
    image = np.full((140,600,3),(90,60,30),dtype=np.uint8)
    templates = {}
    for index,tile in enumerate(tiles):
        x,y = 20+index*75,20
        face = np.full((96,61,3),225,dtype=np.uint8)
        color = (10,10,170) if tile == '0m' else (15,15,15)
        cv2.putText(face,str(index+1),(12,62),cv2.FONT_HERSHEY_SIMPLEX,1.4,color,3)
        image[y:y+96,x:x+61] = face
        filename = 'tile_'+tile+'.png'
        (locator.folder/filename).write_bytes(cv2.imencode('.png',face[8:86,5:53])[1].tobytes())
        templates['tile_'+tile] = {'file':filename}
    locator.path.write_text(json.dumps({'width':600,'height':140,'threshold':.94,
        'verified_sorted_row':True,'hand_region':[0,0,600,140],'templates':templates}))
    state.hand = tiles.copy()
    state.drawn_index = None
    state.public_events = []
    return locator,image


def raw(image):
    return cv2.imencode('.png',image)[1].tobytes()


def test_kuikae_keeps_grey_instances_and_never_clicks_or_learns_them(tmp_path,state):
    locator,image = fixture_row(tmp_path,state)
    for index in (0,3):
        x = 20+index*75
        image[20:116,x:x+61] = (image[20:116,x:x+61]*.6).astype('uint8')
    before = {p.name:p.read_bytes() for p in locator.folder.iterdir()}
    frame = raw(image)
    boxes = locator.hand(frame,state)
    assert len(boxes) == 7 and [i for i,b in boxes.items() if not b.enabled] == [0,3]
    assert locator.discard_tile(frame,state,1).label == 'tile_0m'
    for index in (0,3):
        with pytest.raises(InputDeferred,match='dimmed'):
            locator.discard_tile(frame,state,index)
    assert before == {p.name:p.read_bytes() for p in locator.folder.iterdir()}
    # Parallel observation must not publish a disabled target for later reuse.
    executor = object.__new__(Executor)
    executor.locator = locator
    executor._save_uncalibrated_offers = lambda *args:None
    actions = [SimpleNamespace(kind='discard')]
    targets = executor._recognize_candidates(frame,state,actions)
    assert set(targets) == {('tile',(i,)) for i in (1,2,4,5,6)}
    # A new visual frame invalidates the complete-row cache and enabled flags.
    image[20:116,20:81] = np.minimum(image[20:116,20:81]/.6,255).astype('uint8')
    assert locator.discard_tile(raw(image),state,0).enabled


@pytest.mark.parametrize('damage',['missing','wrong_glyph','uncalibrated_grey'])
def test_grey_row_rejects_count_order_or_missing_independent_glyph(tmp_path,state,damage):
    locator,image = fixture_row(tmp_path,state)
    image[20:116,20:81] = (image[20:116,20:81]*.6).astype('uint8')
    if damage == 'missing':
        image[20:116,170:231] = (90,60,30)
    elif damage == 'wrong_glyph':
        image[20:116,20:81] = (image[20:116,95:156]*.6).astype('uint8')
    else:
        profile = locator.profile()
        del profile['templates']['tile_1m']
        locator.path.write_text(json.dumps(profile))
    before = {p.name:p.read_bytes() for p in locator.folder.iterdir()}
    with pytest.raises(InputDeferred):
        locator.hand(raw(image),state)
    assert before == {p.name:p.read_bytes() for p in locator.folder.iterdir()}


def test_bright_sorted_row_and_draw_instance_still_work(tmp_path,state):
    locator,image = fixture_row(tmp_path,state)
    # The rightmost draw remains independent of sorted concealed instances.
    state.hand = ['5z',*state.hand[:-1]]
    state.drawn_index = 0
    boxes = locator.hand(raw(image),state)
    assert boxes[0].label == 'tile_5z' and boxes[0].center[0] > 450
    assert boxes[2].label == 'tile_0m' and all(b.enabled for b in boxes.values())
