"""Known small hands, independent variants and explicitly blank white faces."""
import json

import cv2
import numpy as np
import pytest

from majsoul_llm.native import InputDeferred
from test_kuikae_locator import fixture_row, raw


def small_row(tmp_path, state, names):
    locator,image = fixture_row(tmp_path,state)
    original = image.copy()
    image[:] = (90,60,30)
    available = state.hand.copy()
    for index,tile in enumerate(names):
        old = 20+available.index(tile)*75
        image[20:116,20+index*75:81+index*75] = original[20:116,old:old+61]
    state.hand = names.copy()
    return locator,image


@pytest.mark.parametrize('names,drawn', [(['1m'],None),(['0m','5m'],1),(['5m','5m'],1)])
def test_known_small_hands_map_exact_red_and_draw_instances(tmp_path,state,names,drawn):
    locator,image = small_row(tmp_path,state,names)
    state.drawn_index = drawn
    before = {p.name:p.read_bytes() for p in locator.folder.iterdir()}
    boxes = locator.hand(raw(image),state)
    assert {i:b.label for i,b in boxes.items()} == {i:'tile_'+t for i,t in enumerate(names)}
    if drawn is not None:
        assert boxes[drawn].center[0] > boxes[0].center[0]
    assert before == {p.name:p.read_bytes() for p in locator.folder.iterdir()}


@pytest.mark.parametrize('dimmed', [False,True])
def test_unknown_small_hand_still_cannot_learn_or_guess(tmp_path,state,dimmed):
    locator,image = small_row(tmp_path,state,['1m','5m'])
    profile=locator.profile()
    del profile['templates']['tile_5m']
    locator.path.write_text(json.dumps(profile))
    if dimmed:
        image[20:116,95:156] = (image[20:116,95:156]*.6).astype('uint8')
    before = {p.name:p.read_bytes() for p in locator.folder.iterdir()}
    with pytest.raises(InputDeferred,match='independently calibrated|anchors'):
        locator.hand(raw(image),state)
    assert before == {p.name:p.read_bytes() for p in locator.folder.iterdir()}


def test_unknown_bright_glyph_still_learns_with_independent_anchors(tmp_path,state):
    locator,image=fixture_row(tmp_path,state)
    profile=locator.profile()
    del profile['templates']['tile_0m']
    locator.path.write_text(json.dumps(profile))
    assert len(locator.hand(raw(image),state)) == 7
    assert locator.profile()['templates']['tile_0m']['source'] == 'verified_sorted_row_anchors'


def test_independent_reflection_variant_keeps_threshold_and_red_identity(tmp_path,state):
    locator,image = small_row(tmp_path,state,['0m','5m'])
    # A moving reflection changes ink as well as background. Only a separately
    # calibrated variant may accept this frame; the threshold is unchanged.
    cv2.line(image,(35,22),(65,110),(250,250,250),14)
    frame=raw(image)
    with pytest.raises(InputDeferred,match='ordering'):
        locator.hand(frame,state)
    patch=image[28:106,25:73]
    (locator.folder/'red-reflection.png').write_bytes(raw(patch))
    profile=locator.profile()
    profile['templates']['tile_0m']['variants']=[{'file':'red-reflection.png'}]
    locator.path.write_text(json.dumps(profile))
    assert locator.hand(frame,state)[0].label == 'tile_0m'
    # Swapping the actual red/plain instances remains a disagreement.
    image[20:116,20:81],image[20:116,95:156] = image[20:116,95:156].copy(),image[20:116,20:81].copy()
    with pytest.raises(InputDeferred,match='ordering'):
        locator.hand(raw(image),state)
    assert locator.profile()['threshold'] == .94


def test_white_dragon_requires_blank_interior_and_can_be_selected_in_riichi(tmp_path,state):
    locator,image=small_row(tmp_path,state,['5z','5m'])
    # Put the white face before the independent drawn tile.
    image[20:116,20:81] = 225
    (locator.folder/'tile_5z.png').write_bytes(raw(np.full((78,48,3),225,np.uint8)))
    state.drawn_index=1
    state.operation.raw={'operation_list':[{'type':7,'combination':['5z']}]}
    assert locator.hand(raw(image),state)[0].label == 'tile_5z'
    assert locator.riichi_tile(raw(image),state,0).center[0] < 90
    image[20:116,95:156]=(image[20:116,95:156]*.6).astype('uint8')
    assert locator.riichi_tile(raw(image),state,0).enabled
    image[20:116,20:81] = (image[20:116,20:81]*.6).astype('uint8')
    with pytest.raises(InputDeferred,match='dimmed'):
        locator.riichi_tile(raw(image),state,0)
    # Constant white correlation would incorrectly report a perfect match.
    cv2.putText(image,'X',(28,85),cv2.FONT_HERSHEY_SIMPLEX,1.6,(10,10,10),5)
    with pytest.raises(InputDeferred,match='ordering'):
        locator.hand(raw(image),state)


def test_constant_nonwhite_template_is_rejected(tmp_path,state):
    locator,image=small_row(tmp_path,state,['1m'])
    (locator.folder/'tile_1m.png').write_bytes(raw(np.full((78,48,3),225,np.uint8)))
    with pytest.raises(InputDeferred,match='no identifying detail'):
        locator.hand(raw(image),state)
