import threading
from types import SimpleNamespace

from majsoul_llm.domain import Phase
from majsoul_llm.input_lease import InputLease
from majsoul_llm.lobby import WaitingUser
from majsoul_llm.round_flow import RoundFlow


async def test_result_title_before_button_does_not_create_submission_or_abandon_flow(store,state,tmp_path):
    state.phase=Phase.ENDED
    attempts=[]
    async def observe():
        return 'final' if len(attempts)<2 else 'room'
    async def image_bytes():
        return b'isolated-result-image'
    async def click(scene,control,ctx,*,submit):
        attempts.append((scene,control))
        assert not store.records('result_ui')
        if len(attempts)==1:
            raise WaitingUser('confirm_button_not_visible_yet')
        ctx.before_submit(submit)
        assert store.records('result_ui')[0]['payload']['status']=='submitted'
    service=SimpleNamespace(engine=SimpleNamespace(state=state),store=store,
        settings=SimpleNamespace(runtime=tmp_path),browser=SimpleNamespace(image_bytes=image_bytes),
        executor=SimpleNamespace(mouse=SimpleNamespace(stop=threading.Event())),
        input_lease=InputLease(),lobby_ui=SimpleNamespace(observe=observe,click=click))
    await RoundFlow(service).confirm_pages(state.match_id,state.round_uid)
    assert attempts==[('final','confirm')]*2
    assert len(store.records('result_ui'))==1
    assert service.input_lease.owner is None
