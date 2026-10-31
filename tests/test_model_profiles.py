import json
import socket
from dataclasses import dataclass

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from majsoul_llm.config import Settings
from majsoul_llm.model import ModelClient
from majsoul_llm.model_profiles import ModelProfile, ModelProfiles, ProfileError
from majsoul_llm.store import Store


@dataclass
class Local:
    settings: Settings
    profiles: ModelProfiles


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.delenv('MAJSOUL_API_KEY', raising=False)
    s = Settings(runtime=tmp_path, port=0, api_key=SecretStr('fixture-old-key'),
                 compact_validated=True, table_wire_validated=True, json_schema=True, stream_actions=True)
    s.save_private()
    return Local(s, ModelProfiles(s))


def install_new(local):
    local.profiles.capture('baseline')
    data = json.loads(local.profiles.path.read_text(encoding='utf-8'))
    data['profiles']['other'] = {**data['profiles']['baseline'], 'provider':'other',
        'model':'fixture-new-model', 'base_url':'https://other.example/v2', 'api_key':'fixture-new-key'}
    local.profiles.path.write_text(json.dumps(data), encoding='utf-8')


def test_select_and_exact_rollback_keep_only_matching_gates(local):
    install_new(local)
    previous = local.settings.control_token
    selected = local.profiles.select('other')
    assert not any(selected['format_gates'].values())
    new = Settings.load(local.settings.runtime/'private.json')
    assert new.model == 'fixture-new-model' and new.model_provider == 'other'
    assert new.api_key.get_secret_value() == 'fixture-new-key'
    assert new.control_token == previous and new.stream_hedge_delay_seconds == 0
    restored = ModelProfiles(new).select('baseline')
    assert all(restored['format_gates'].values())
    loaded = Settings.load(local.settings.runtime/'private.json')
    assert loaded.model == local.settings.model and loaded.api_key == local.settings.api_key
    assert 'fixture-old-key' not in json.dumps(local.profiles.list())
    assert 'fixture-new-key' not in json.dumps(local.profiles.list())
    assert 'api_key' not in new.model_dump_json()


def test_running_service_or_active_archive_does_not_write(local, state):
    install_new(local)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        listener.listen()
        local.settings.port = listener.getsockname()[1]
        before = (local.settings.runtime/'private.json').read_bytes()
        with pytest.raises(ProfileError, match='stop the local service'):
            local.profiles.select('other')
        assert (local.settings.runtime/'private.json').read_bytes() == before
    local.settings.port = 0
    store = Store(local.settings.runtime/'archive.sqlite')
    store.save_snapshot(state)
    store.close()
    with pytest.raises(ProfileError, match='active archived match'):
        local.profiles.select('other')
    assert (local.settings.runtime/'private.json').read_bytes() == before


@pytest.mark.parametrize('url', ['https://private-key@example.com/v1', 'https://example.com/v1?key=private-key',
    'https://example.com/#private-key', 'http://remote.example/v1', 'https://example.com:bad/v1'])
def test_url_rejection_withholds_validation_input(url):
    with pytest.raises(ValidationError) as caught:
        Settings(base_url=url)
    assert url not in str(caught.value)


def test_protocol_rejection_and_environment_override_invalidation(local, monkeypatch):
    install_new(local)
    profile = local.profiles.load().profiles['baseline']
    with pytest.raises(ValidationError):
        ModelProfile(**{**profile.model_dump(), 'api_key':'fixture-key', 'api_style':'chat_completions'})
    local.profiles.select('baseline')
    monkeypatch.setenv('MAJSOUL_API_KEY', 'fixture-environment-key')
    with pytest.raises(ProfileError, match='unset MAJSOUL_API_KEY'):
        local.profiles.select('other')
    loaded = Settings.load(local.settings.runtime/'private.json')
    assert not loaded.compact_validated and not loaded.stream_actions


async def test_selected_profile_routes_decision_and_review_with_none(local):
    install_new(local)
    local.profiles.select('other')
    settings = Settings.load(local.settings.runtime/'private.json')
    requests = []
    async def handler(request):
        requests.append(request)
        return httpx.Response(200, text='data: '+json.dumps({'type':'response.completed','response':{
            'status':'completed', 'usage':{'input_tokens':1, 'output_tokens':1},
            'output':[{'content':[{'type':'output_text', 'text':'{"ok":true}'}]}]}})+'\n\n')
    client = ModelClient(settings, transport=httpx.MockTransport(handler))
    try:
        for purpose in ('decision', 'repair', 'review'):
            result, metrics = await client.request({'q':'fixture'}, purpose=purpose)
            assert result == {'ok':True}
            assert metrics['model_config']['profile'] == 'other'
        assert len(requests) == 3
        for request in requests:
            assert str(request.url) == 'https://other.example/v2/responses'
            assert request.headers['authorization'] == 'Bearer fixture-new-key'
            body = json.loads(request.content)
            assert body['model'] == 'fixture-new-model' and body['reasoning'] == {'effort':'none'}
    finally:
        await client.close()
