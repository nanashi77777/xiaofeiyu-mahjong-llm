import threading
from types import SimpleNamespace

import pytest

from majsoul_llm.domain import Phase
from majsoul_llm.input_lease import InputLease
from majsoul_llm.round_flow import RoundFlow


def fixture_service(tmp_path,store,state,scenes,*,stop_after=None,frame=b'fixture-frame'):
    state = state.model_copy(update={'lifecycle':'ended','phase':Phase.ENDED})
    stop = threading.Event()
    service = SimpleNamespace(settings=SimpleNamespace(runtime=tmp_path),store=store,
        engine=SimpleNamespace(state=state),executor=SimpleNamespace(mouse=SimpleNamespace(stop=stop)),
        input_lease=InputLease())
    calls = []
    sequence = iter(scenes)
    class UI:
        async def observe(self):
            return next(sequence)
        async def click(self,scene,control,ctx,*,submit):
            ctx.before_submit(submit)
            calls.append(scene)
            if scene==stop_after:
                stop.set()
    class Browser:
        async def image_bytes(self):
            return frame
    service.lobby_ui,service.browser = UI(),Browser()
    return service,calls


async def test_final_then_reward_each_confirm_once_and_return_to_room(tmp_path,store,state):
    service,calls = fixture_service(tmp_path,store,state,
        ['final','final','final_reward','final_reward','room'])
    flow = RoundFlow(service)
    await flow.confirm_pages(state.match_id,state.round_uid)
    assert calls==['final','final_reward'] and flow.last_scene=='room'
    rows = list(reversed(store.records('result_ui',state.match_id)))
    assert [r['payload']['scene'] for r in rows]==['final','final_reward']
    assert len({r['payload']['screenshot'] for r in rows})==2
    assert all((tmp_path/'results'/r['payload']['screenshot']).read_bytes()==b'fixture-frame' for r in rows)


async def test_stop_after_final_never_confirms_following_reward(tmp_path,store,state):
    service,calls = fixture_service(tmp_path,store,state,['final','final_reward','room'],stop_after='final')
    await RoundFlow(service).confirm_pages(state.match_id,state.round_uid)
    assert calls==['final']
    assert store.records('result_ui')[0]['payload']['status']=='deferred'


async def test_reward_requires_authoritative_match_end(tmp_path,store,state):
    service,calls = fixture_service(tmp_path,store,state,['final_reward','room'])
    service.engine.state.lifecycle = 'active'
    service.engine.state.phase = Phase.ROUND_END
    await RoundFlow(service).confirm_pages(state.match_id,state.round_uid)
    assert not calls and not store.records('result_ui')


async def test_new_round_invalidates_reward_confirmation(tmp_path,store,state):
    service,calls = fixture_service(tmp_path,store,state,['final_reward','room'])
    original = service.lobby_ui.click
    async def changed(scene,control,ctx,*,submit):
        service.engine.state.round_uid = 'fixture-new-round'
        await original(scene,control,ctx,submit=submit)
    service.lobby_ui.click = changed
    await RoundFlow(service).confirm_pages(state.match_id,state.round_uid)
    assert not calls and store.records('result_ui')[0]['payload']['status']=='deferred'


@pytest.mark.parametrize('scene',['final','final_reward'])
async def test_explicit_resume_preserves_both_original_frames(tmp_path,store,state,scene):
    for frame in (b'first-frame',b'changed-frame'):
        service,calls = fixture_service(tmp_path,store,state,[scene,'room'],frame=frame)
        await RoundFlow(service).confirm_pages(state.match_id,state.round_uid)
        assert calls==[scene]
    rows = store.records('result_ui',state.match_id)
    assert len({r['payload']['screenshot'] for r in rows})==2
    assert {(tmp_path/'results'/r['payload']['screenshot']).read_bytes() for r in rows}=={b'first-frame',b'changed-frame'}
