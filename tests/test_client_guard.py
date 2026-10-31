import threading
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from majsoul_llm.client_guard import ClientGuard
from majsoul_llm.config import Settings
from majsoul_llm.coordinator import Coordinator
from majsoul_llm.input_lease import InputLease


@pytest.mark.parametrize('user_stops',[False,True])
async def test_visible_away_recovery_never_overrides_a_later_user_stop(store,state,user_stops):
    state.operation=None
    engine=SimpleNamespace(store=store,state=state)
    lease=InputLease()
    executor=SimpleNamespace(mouse=SimpleNamespace(stop=threading.Event()),lease=lease)
    coord=Coordinator(Settings(api_key=SecretStr('fixture-credential')),engine,None,executor)
    coord.enabled=True
    clicked=[]
    async def observe():
        return 'away'
    async def click(scene,control,ctx):
        assert lease.owner=='client-away:'+state.match_id
        assert not coord.enabled and coord.status=='client_away_notice'
        ctx.check()
        if user_stops:
            await coord.stop('user_stop')
            ctx.check()
        clicked.append((scene,control))
    service=SimpleNamespace(engine=engine,store=store,coordinator=coord,input_lease=lease,
                            executor=executor,lobby_ui=SimpleNamespace(observe=observe,click=click))
    guard=ClientGuard(service)
    await guard.check(state.match_id,coord.generation)
    assert lease.owner is None
    assert coord.enabled == (not user_stops)
    assert clicked == ([] if user_stops else [('away','resume')])
    assert not store.records('decision') and store.records('client_away')
