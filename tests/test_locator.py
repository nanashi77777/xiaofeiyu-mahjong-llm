import cv2
import numpy as np
import pytest

from majsoul_llm.locator import ScreenLocator, glyph_similarity
from majsoul_llm.native import InputDeferred


def png(image):
    return cv2.imencode('.png',image)[1].tobytes()


def test_canvas_resampling_keeps_dot_glyph_distinct():
    patch=np.full((78,48,3),210,dtype=np.uint8)
    for x,y in [(12,10),(24,22),(36,34),(12,46),(36,46),(12,64),(36,64)]:
        cv2.circle(patch,(x,y),5,(15,20,20),2)
    resized=cv2.resize(cv2.resize(patch,(37,61)),(48,78))
    wrong=np.full_like(patch,210)
    cv2.putText(wrong,'4',(7,55),cv2.FONT_HERSHEY_SIMPLEX,1.7,(15,20,20),3)
    assert glyph_similarity(resized,patch)>.91
    assert glyph_similarity(resized,wrong)<.8


def test_visual_instances_red_draw_slot_and_ambiguity(tmp_path,state):
    random=np.random.default_rng(812)
    names=['0m','5m','5m']
    patches={t:random.integers(20,240,(40,25,3),dtype=np.uint8) for t in set(names)}
    screen=np.zeros((120,250,3),dtype=np.uint8)
    for i,tile in enumerate(names):
        screen[65:105,20+60*i:45+60*i]=patches[tile]
    locator=ScreenLocator(tmp_path)
    raw=png(screen)
    locator.save_crop(raw,'tile_0m',[20,65,25,40],hand_region=[0,50,250,70])
    locator.save_crop(raw,'tile_5m',[80,65,25,40])
    state.hand=names
    state.drawn_index=2
    found=locator.hand(raw,state)
    assert len(found)==3 and found[2].center[0]==152.5 and found[0].label=='tile_0m'
    with pytest.raises(InputDeferred):
        locator.button(raw,'riichi')
    state.hand=['0m','5m','4m']
    with pytest.raises(InputDeferred):
        locator.hand(raw,state)
