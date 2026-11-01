"""Control-only monitoring avoids full archive/usage work and retains failures."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from majsoul_llm.app import Service, create_app
from majsoul_llm.config import Settings


def test_live_progress_does_not_aggregate_history_and_record_revision_advances(store,state):
    service=object.__new__(Service)
    service.settings=Settings()
    service.engine=SimpleNamespace(state=state,lobby_session=SimpleNamespace(public=lambda:{'authenticated':True}))
    service.browser=SimpleNamespace(connected=True)
    service.coordinator=SimpleNamespace(enabled=True,status='deciding')
    service.runtime_build={'git_head':'fixture','source_digest':'fixture'}
    service.navigator=SimpleNamespace(command_id=None,busy=lambda:False)
    service.input_lease=SimpleNamespace(owner=None)
    service.round_flow=SimpleNamespace(task=None,last_scene='room')
    service.store=store
    # Full status requires these methods. The progress path must not use them.
    store.records=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('history scan'))
    before=service.progress()
    assert before['state']['hand']==state.hand and before['enabled']
    assert not {'token_usage','usage_summary','receipts','decision_annotations','plan','model_config'} & before.keys()
    store.record('decision_failure',{'window_id':'fixture','detail':'fixture'},state.match_id,state.round_uid)
    assert service.progress()['records_revision']>before['records_revision']
    client=TestClient(create_app(service.settings,service))
    for token in (service.settings.read_token,service.settings.navigation_token):
        assert client.get('/control/progress',headers={'Authorization':'Bearer '+token.get_secret_value()}).status_code==401
    result=client.get('/control/progress',headers={'Authorization':'Bearer '+service.settings.control_token.get_secret_value()})
    assert result.status_code==200 and result.json()['state']['hand']==state.hand
