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
    assert locator.hand(raw,state)==found and locator.hand_cache_hits==1
    # Opponent events may change the state version without moving our hand.
    state.state_version+=1
    assert locator.hand(raw,state)==found and locator.hand_cache_hits==2
    changed=screen.copy()
    changed[65:105,140:165]=0
    with pytest.raises(InputDeferred):
        locator.hand(png(changed),state)
    state.drawn_index=None
    locator.hand(raw,state)
    assert locator.hand_cache_hits==2
    with pytest.raises(InputDeferred):
        locator.button(raw,'riichi')
    state.hand=['0m','5m','4m']
    with pytest.raises(InputDeferred):
        locator.hand(raw,state)


def test_dynamic_menu_reads_all_choices_and_rejects_red_or_candidate_mismatch(tmp_path):
    import json
    screen = np.zeros((723,1284,3),dtype=np.uint8)
    for x in (510,564,644,698):
        screen[477:553,x:x+51] = 220
    locator = ScreenLocator(tmp_path)
    locator.folder.mkdir(parents=True)
    locator.path.write_text(json.dumps({'width':1284,'height':723,'templates':{},
                                        'combo_region':[450,460,390,110]}))
    class Classifier:
        predictions = [('1s',1.),('3s',1.),('3s',1.),('4s',1.)]
        def predict(self, images):
            assert len(images) == 4
            return self.predictions
    locator.classifier = Classifier()
    raw = png(screen)
    chosen = locator.combo(raw,'chi',['3s','4s'],[['1s','3s'],['3s','4s']])
    assert chosen.center[0] > 640 and chosen.label == 'combo_dynamic'
    with pytest.raises(InputDeferred,match='complete server choices'):
        locator.combo(raw,'chi',['3s','4s'],[['3s','4s']])
    with pytest.raises(InputDeferred,match='not uniquely'):
        locator.combo(raw,'chi',['3s','0s'])
    locator.classifier.predictions[0] = ('1s',.98)
    with pytest.raises(InputDeferred,match='uncertain'):
        locator.combo(raw,'chi',['3s','4s'])


def test_button_variants_deduplicate_one_target_but_never_choose_between_locations(tmp_path):
    import json
    rng = np.random.default_rng(664)
    glyph = rng.integers(20,240,(35,44,3),dtype=np.uint8)
    second = rng.integers(20,240,(35,44,3),dtype=np.uint8)
    screen = np.zeros((120,280,3),dtype=np.uint8)
    screen[40:75,30:74] = glyph
    locator = ScreenLocator(tmp_path)
    raw = png(screen)
    locator.save_crop(raw,'button_ankan',[30,40,44,35])
    profile = locator.profile()
    cv2.imencode('.png', second)[1].tofile(str(locator.folder/'variant.png'))
    profile['templates']['button_ankan']['variants'] = [
        {'file':'button_ankan.png'}, {'file':'variant.png'}]
    locator.path.write_text(json.dumps(profile))
    assert locator.button(raw,'ankan').center == (52,57.5)
    screen[40:75,30:74] = second
    assert locator.button(png(screen),'ankan').center == (52,57.5)
    screen[40:75,170:214] = glyph
    with pytest.raises(InputDeferred,match='ambiguous'):
        locator.button(png(screen),'ankan')
    with pytest.raises(InputDeferred,match='absent'):
        locator.button(png(np.zeros_like(screen)),'ankan')


def test_matching_combo_crop_still_requires_complete_current_menu(tmp_path):
    from majsoul_llm.locator import Box
    rng = np.random.default_rng(665)
    screen = np.zeros((120,280,3),dtype=np.uint8)
    screen[40:75,30:74] = rng.integers(20,240,(35,44,3),dtype=np.uint8)
    locator = ScreenLocator(tmp_path)
    raw = png(screen)
    locator.save_crop(raw,'combo_pon_0p_5p',[30,40,44,35])
    group = Box(20,30,80,65,1.,'combo_dynamic')
    locator.menu_groups = lambda raw:[(['0p','5p'],group)]
    with pytest.raises(InputDeferred,match='complete server choices'):
        locator.combo(raw,'pon',['0p','5p'],[['0p','5p'],['5p','5p']])
    assert locator.combo(raw,'pon',['0p','5p'],[['0p','5p']]).center == (52,57.5)
    locator.menu_groups = lambda raw:[(['5p','5p'],group)]
    with pytest.raises(InputDeferred):
        locator.combo(raw,'pon',['0p','5p'],[['0p','5p']])
    locator.menu_groups = lambda raw:[(['0p','5p'],Box(150,30,80,65,1.,'combo_dynamic'))]
    with pytest.raises(InputDeferred,match='disagrees'):
        locator.combo(raw,'pon',['0p','5p'],[['0p','5p']])
