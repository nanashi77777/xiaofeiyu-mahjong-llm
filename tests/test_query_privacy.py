import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from conftest import event
from majsoul_llm.app import create_app
from majsoul_llm.config import Settings
from majsoul_llm.memory import Memories
from majsoul_llm.queries import Queries


def test_current_and_interrupted_rounds_hide_all_private_prose_and_draws(store,state):
    hidden = 'fixture-private-hand-and-plan'
    store.save_event(event('ActionNewRound',{'tiles':state.hand,'private_note':hidden}),state)
    store.record('decision',{'decision':{'reason':hidden,'plan_update':{'goal':hidden}},
                             'action':{'features':{'private_note':hidden}}},state.match_id,state.round_uid)
    draw = event('ActionDealTile',{'seat':0,'tile':'0p','private_note':hidden},2)
    store.save_event(draw,state)
    query = Queries(store,'fixture')
    for lifecycle in ('active','interrupted'):
        state.lifecycle = lifecycle
        store.save_snapshot(state)
        data = query.round(state.round_uid)['data']
        text = json.dumps(data,ensure_ascii=False)
        assert hidden not in text and '0p' not in text and 'tiles' not in text
        assert data['decisions'] == [] and data['active_private_fields_hidden']
        assert query.live()['data']['final_result'] is None
    state.lifecycle = 'ended'
    store.save_snapshot(state)
    data = query.round(state.round_uid)['data']
    assert hidden in json.dumps(data,ensure_ascii=False)
    assert not data['active_private_fields_hidden']


def test_resumed_source_hides_memory_list_detail_and_all_revisions(store,state):
    state.lifecycle = 'ended'
    store.save_event(event('ActionNewRound',{'tiles':state.hand}),state)
    memory = Memories(store)
    first = memory.revise({'claim':'fixture-historical-private-plan','supports':['e1']},state.match_id)
    memory.revise({'id':first['id'],'revision':1,'status':'inactive','reason':'fixture-retired-private-reason'},state.match_id)
    query = Queries(store,'fixture')
    assert len(query.memories(id=first['id'])['data']['revisions']) == 2
    assert query.memories(status='inactive')['data'][0]['status'] == 'inactive'
    for lifecycle in ('active','interrupted'):
        state.lifecycle = lifecycle
        store.save_snapshot(state)
        assert query.memories(id=first['id'])['data'] is None
        assert query.memories(status='inactive')['data'] == []
        assert query.memories('fixture-historical')['data'] == []


def test_query_and_navigation_tokens_cannot_reach_any_gameplay_control(store):
    settings = Settings()
    service = SimpleNamespace(queries=Queries(store,'fixture'),status=lambda:{'status':'fixture-only'},
                              navigator=SimpleNamespace(status=lambda:{'capabilities':['login']}))
    client = TestClient(create_app(settings,service))
    for secret in (settings.read_token,settings.navigation_token):
        headers = {'Authorization':'Bearer '+secret.get_secret_value()}
        for action in ('start','shadow','pause','stop','emergency','input-mode','room','bind','browser','confirm-results'):
            assert client.post('/control/'+action,json={},headers=headers).status_code == 401
        assert client.get('/control/status',headers=headers).status_code == 401
    reader = {'Authorization':'Bearer '+settings.read_token.get_secret_value()}
    nav = {'Authorization':'Bearer '+settings.navigation_token.get_secret_value()}
    assert client.get('/api/v1/mahjong/navigation',headers=reader).status_code == 401
    assert client.get('/api/v1/mahjong/navigation',headers=nav).status_code == 200
    assert client.get('/api/v1/mahjong/live',headers=reader).status_code == 200
    assert client.get('/api/v1/mahjong/live',headers=nav).status_code == 401
    assert client.post('/control/stop',json={},headers={**reader,'Origin':'https://fixture.invalid'}).status_code == 403
