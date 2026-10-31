import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from majsoul_llm.execution import Executor
from majsoul_llm.locator import ScreenLocator
from majsoul_llm.native import InputDeferred


def test_frame_reuse_never_reuses_another_image_or_normalization_size(tmp_path):
    locator=ScreenLocator(tmp_path)
    first=cv2.imencode('.png',np.full((60,100,3),70,np.uint8))[1].tobytes()
    second=cv2.imencode('.png',np.full((60,100,3),180,np.uint8))[1].tobytes()
    profile={'width':100,'height':60}
    a=locator.normalized(first,profile)
    assert locator.normalized(first,profile)[0] is a[0]
    assert not a[0].flags.writeable
    b=locator.normalized(second,profile)
    assert np.all(b[0]==180) and b[0] is not a[0]
    c=locator.normalized(second,{'width':50,'height':30})
    assert c[0].shape==(30,50,3) and c[0] is not b[0]
    with pytest.raises(InputDeferred,match='aspect ratio'):
        locator.normalized(second,{'width':50,'height':50})


def test_missing_button_evidence_keeps_first_mature_frame_once_per_window(state,tmp_path):
    executor=object.__new__(Executor)
    executor.offered_window,executor.offered_saved=None,set()
    executor.browser=SimpleNamespace(settings=SimpleNamespace(runtime=tmp_path))
    executor.locator=SimpleNamespace(profile=lambda:{'templates':{'button_pass':{}}})
    actions=[SimpleNamespace(kind='tsumo'),SimpleNamespace(kind='pass')]
    state.operation.received_mono=time.monotonic()
    executor._save_uncalibrated_offers(b'too early',state,actions)
    assert not (tmp_path/'calibration').exists()
    state.operation.received_mono-=1
    executor._save_uncalibrated_offers(b'actual frame',state,actions)
    executor._save_uncalibrated_offers(b'different frame',state,actions)
    folder=tmp_path/'calibration'/'offered'
    assert len(list(folder.glob('*.png')))==1
    assert next(folder.glob('*.png')).read_bytes()==b'actual frame'
    data=json.loads(next(folder.glob('*.json')).read_text(encoding='utf8'))
    assert data['missing_buttons']==['tsumo'] and data['extra_captures']==0
    state.operation.window_id='new-window'
    executor._save_uncalibrated_offers(b'new frame',state,actions)
    assert len(list(folder.glob('*.png')))==2
