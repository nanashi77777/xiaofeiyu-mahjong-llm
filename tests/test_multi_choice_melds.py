"""Constructed menu images exercise grouping and the real action transaction.

The marker classifier is deliberately a fixture; real glyph classification is
covered separately with the archived client menu, not claimed by these images.
"""
import json
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from majsoul_llm.domain import Phase
from majsoul_llm.execution import Executor, Target
from majsoul_llm.input_lease import InputLease
from majsoul_llm.locator import ScreenLocator
from majsoul_llm.native import InputDeferred
from majsoul_llm.rules import legal_actions
from majsoul_llm.streaming import ActionPrefix
from majsoul_llm.wire import build_context


def menu(tmp_path, choices, *, wrapped=False, anchor_calibration=False):
    image = np.zeros((723,1284,3), dtype=np.uint8)
    codes = {name:i+30 for i,name in enumerate(sorted({t for group in choices for t in group}))}
    names = {code:name for name,code in codes.items()}
    for i,group in enumerate(choices):
        row,col = divmod(i,3) if wrapped else (0,i)
        for j,tile in enumerate(group):
            x,y = 220+col*150+j*45, 470-row*100 if anchor_calibration else 340+row*100
            image[y:y+70,x:x+40] = 230
            image[y+34:y+37,x+19:x+22] = (0,0,codes[tile])
    locator = ScreenLocator(tmp_path)
    locator.folder.mkdir(parents=True, exist_ok=True)
    profile = {'width':1284,'height':723,'templates':{},'combo_region':[170,300,950,270]}
    if anchor_calibration:
        profile.update(combo_region=[450,460,390,110],hand_region=[100,600,1100,120])
        # Visible concealed-hand faces must never become menu choices.
        image[610:680,300:340] = 230
    locator.path.write_text(json.dumps(profile))
    class MarkerClassifier:
        def predict(self, images):
            return [(names[int(im[im.shape[0]//2,im.shape[1]//2,2])], 1.) for im in images]
    locator.classifier = MarkerClassifier()
    return locator, cv2.imencode('.png',image)[1].tobytes()


@pytest.mark.parametrize('wrapped',[False,True])
def test_existing_two_choice_anchor_covers_all_five_choices_and_excludes_hand(tmp_path,wrapped):
    choices = [['0m','6m'],['2m','3m'],['3m','5m'],['5m','6m'],['3m','0m']]
    locator,raw = menu(tmp_path,choices,wrapped=wrapped,anchor_calibration=True)
    groups = locator.menu_groups(raw)
    assert len(groups) == 5
    assert {tuple(sorted(t)) for t,_ in groups} == {tuple(sorted(t)) for t in choices}
    for tiles in choices:
        locator.combo(raw,'chi',tiles,choices)


def test_menu_clipped_at_observation_band_is_rejected(tmp_path):
    locator,raw = menu(tmp_path,[['2m','3m'],['3m','5m']],anchor_calibration=True)
    image = cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)
    image[510:580,1000:1040] = 230
    with pytest.raises(InputDeferred,match='boundary'):
        locator.menu_groups(cv2.imencode('.png',image)[1].tobytes())


@pytest.mark.parametrize('wrapped',[False,True])
def test_three_and_five_choices_keep_all_red_variants_and_reject_missing_duplicate_or_extra(tmp_path,wrapped):
    choices = [['0m','6m'],['2m','3m'],['3m','5m'],['5m','6m'],['3m','0m']]
    locator, raw = menu(tmp_path,choices,wrapped=wrapped)
    groups = locator.menu_groups(raw)
    assert len(groups) == 5
    assert {tuple(sorted(tiles)) for tiles,_ in groups} == {tuple(sorted(t)) for t in choices}
    for tiles in choices:
        found = locator.combo(raw,'chi',tiles,choices)
        assert found.center in {box.center for names,box in groups if sorted(names)==sorted(tiles)}
    with pytest.raises(InputDeferred,match='complete server choices'):
        locator.combo(raw,'chi',choices[0],choices[:-1])
    with pytest.raises(InputDeferred,match='complete server choices'):
        locator.combo(raw,'chi',choices[0],[*choices,['7m','8m']])
    duplicate_locator, duplicate = menu(tmp_path/'duplicate',[choices[0],choices[0],choices[1]])
    with pytest.raises(InputDeferred,match='complete server choices'):
        duplicate_locator.combo(duplicate,'chi',choices[0],[choices[0],choices[1]])


@pytest.mark.parametrize('selection',[0,1,2])
async def test_three_chi_end_to_end_preserves_model_selected_consumption_despite_menu_order(state,tmp_path,selection):
    state.hand='3m 4m 6m 7m 2p 3p 5s 6s 7s 1z 1z 2z 3z'.split()
    state.drawn_index=None
    state.phase=Phase.RESPONSE
    state.context['last_discard']={'seat':3,'tile':'5m','event_id':'offer'}
    offered=[['3m','4m'],['4m','6m'],['6m','7m']]
    state.operation.raw['operation_list']=[{'type':2,'combination':['|'.join(t) for t in offered]}]
    actions=legal_actions(state)
    chi=[a for a in actions if a.kind=='chi']
    assert len(chi)==3 and actions[-1].kind=='pass'
    context=build_context(state,actions)
    chosen=chi[selection]
    short_id=next(key for key,value in context.actions.items() if value.id==chosen.id)
    selected=ActionPrefix(context,'short').feed(json.dumps({'a':short_id})[:-1]+',')
    assert selected is chosen
    # The UI order deliberately differs from the server candidate order.
    locator, raw=menu(tmp_path,[offered[2],offered[0],offered[1]])
    clicked=[]
    layout={'canvas':{'x':0,'y':0}}
    async def metrics():
        return layout
    async def click(point,deadline,stop,validate,on_press=None):
        await validate()
        if on_press:
            on_press()
        clicked.append(point)
        return time.monotonic()
    executor=object.__new__(Executor)
    executor.state_getter=lambda:state
    executor.lease=InputLease()
    executor.locator=locator
    executor.input_mode='browser_background'
    executor.mouse=SimpleNamespace(stop=threading.Event(),user=SimpleNamespace(GetAsyncKeyState=lambda key:0))
    executor.browser=SimpleNamespace(background_click=click,layout_metrics=metrics,settings=SimpleNamespace(runtime=tmp_path))
    targets=[]
    async def target(semantic,current,indices=None,deadline=None):
        targets.append((semantic,indices))
        if semantic=='chi':
            point=(100,100)
        else:
            assert semantic=='combo:chi'
            tiles=[current.hand[i] for i in indices]
            point=locator.combo(raw,'chi',tiles,offered).center
        return Target(point,(0,0,0,0),time.monotonic(),'constructed_menu_fixture',point,layout)
    executor.target=target
    await executor.execute(state,selected,time.monotonic()+2)
    assert targets==[('chi',None),('combo:chi',chosen.consume_indices)]
    assert len(clicked)==2 and clicked[0]==(100,100)
    expected=locator.combo(raw,'chi',[state.hand[i] for i in chosen.consume_indices],offered).center
    assert clicked[1]==expected and executor.lease.owner is None


def test_red_five_chi_variants_all_reach_independent_model_context(state):
    state.hand='2m 3m 5m 0m 6m 1p 2p 3p 5s 6s 7s 1z 1z'.split()
    state.phase=Phase.RESPONSE
    state.context['last_discard']={'seat':3,'tile':'4m','event_id':'offer'}
    offered=['2m|3m','3m|5m','3m|0m','5m|6m','0m|6m']
    state.operation.raw['operation_list']=[{'type':2,'combination':offered}]
    actions=legal_actions(state)
    context=build_context(state,actions)
    actual={tuple(sorted(state.hand[i] for i in a.consume_indices)) for a in context.actions.values() if a.kind=='chi'}
    assert actual=={tuple(sorted(t.split('|'))) for t in offered}
    assert len(actual)==5 and any(a.kind=='pass' for a in context.actions.values())
