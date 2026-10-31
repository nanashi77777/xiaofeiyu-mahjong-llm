import json

import cv2
import numpy as np
import pytest

from majsoul_llm.locator import ScreenLocator
from majsoul_llm.native import InputDeferred


def test_riichi_resampled_dots_require_bright_complete_instances(tmp_path,state):
    patch=np.full((78,48,3),225,dtype=np.uint8)
    for x,y in [(12,10),(36,10),(12,28),(36,28),(12,46),(36,46),(12,64),(36,64)]:
        cv2.circle(patch,(x,y),5,(15,20,20),2)
    softened=cv2.resize(cv2.resize(patch,(37,61)),(48,78))
    screen=np.zeros((120,250,3),dtype=np.uint8)
    screen[30:108,20:68]=softened
    screen[30:108,160:208]=softened
    locator=ScreenLocator(tmp_path)
    locator.folder.mkdir()
    (locator.folder/'tile_8p.png').write_bytes(cv2.imencode('.png',patch)[1].tobytes())
    locator.path.write_text(json.dumps({'width':250,'height':120,'threshold':.91,
        'hand_region':[0,0,250,120],'templates':{'tile_8p':{'file':'tile_8p.png'}}}))
    state.hand=['8p','8p']
    state.drawn_index=1
    state.operation.raw={'operation_list':[{'type':7,'combination':['8p']}]}
    def raw():
        return cv2.imencode('.png',screen)[1].tobytes()
    assert locator.riichi_tile(raw(),state,0).center[0]<100
    assert locator.riichi_tile(raw(),state,1).center[0]>150
    screen[30:108,160:208]=(softened*.65).astype('uint8')
    with pytest.raises(InputDeferred,match='completely visible'):
        locator.riichi_tile(raw(),state,1)
    screen[30:108,20:68]=(softened*.65).astype('uint8')
    with pytest.raises(InputDeferred,match='not visible'):
        locator.riichi_tile(raw(),state,0)
    state.operation.raw['operation_list'][0]['combination']=['5p']
    with pytest.raises(InputDeferred,match='not offered'):
        locator.riichi_tile(raw(),state,0)
