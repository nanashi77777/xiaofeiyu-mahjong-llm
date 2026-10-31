import importlib
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from majsoul_llm.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def audit_tools(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'tools'))
    return importlib.import_module('audit_secrets'), importlib.import_module('github_sync')


def private_fixture(root):
    data = {'base_url': 'https://sensitive-provider.example.net/v1', 'model': 'private-fixture-model',
            'api_key': 'credential-only-in-local-fixture', 'control_token': 'local-control-fixture',
            'model_profile': 'fixture-baseline', 'model_provider': 'fixture-vendor'}
    path = root / 'runtime/private.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding='utf-8')
    return data


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def test_repository_default_and_example_cannot_route_to_real_provider():
    default = Settings()
    example = Settings.model_validate_json((ROOT / 'config.example.json').read_text(encoding='utf-8'))
    assert default.base_url == example.base_url == 'https://model-provider.invalid/v1'
    assert default.model == example.model == 'your-model-id'
    assert not default.api_key.get_secret_value() and not example.api_key.get_secret_value()


def test_private_model_identity_and_validation_survive_placeholder_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv('MAJSOUL_API_KEY', raising=False)
    path = tmp_path / 'private.json'
    settings = Settings(runtime=tmp_path, base_url='https://private-fixture.example.net/v1',
                        model='private-fixture-model', api_key=SecretStr('fixture-credential'), stream_actions=True)
    settings.model_validation_identity = settings.model_identity()
    settings.save_private(path)
    before = path.read_bytes()
    loaded = Settings.load(path)
    assert loaded.model_identity() == settings.model_identity() and loaded.stream_actions
    assert loaded.base_url == settings.base_url and loaded.model == settings.model
    assert path.read_bytes() == before


def test_audit_matches_actual_values_without_revealing_them(tmp_path, audit_tools):
    audit, _ = audit_tools
    data = private_fixture(tmp_path)
    profiles = {'profiles': {'unused-private-profile': {**data, 'model': 'other-private-model'}}}
    (tmp_path / 'runtime/model-profiles.json').write_text(json.dumps(profiles), encoding='utf-8')
    rules = audit.rules_for(tmp_path)
    for value, rule in [(data['api_key'], 'local-credential'), (data['control_token'], 'local-credential'),
                        ('SENSITIVE-PROVIDER.EXAMPLE.NET', 'local-provider'), (data['model'], 'local-model'),
                        ('other-private-model', 'local-model'), ('unused-private-profile', 'local-model-profile')]:
        assert rules[rule].search(value.encode())
        assert value not in rule
    assert not any(pattern.search((ROOT / 'config.example.json').read_bytes()) for pattern in rules.values())


def test_audit_reads_staged_content_even_when_worktree_is_cleaned(tmp_path, audit_tools, monkeypatch, capsys):
    audit, _ = audit_tools
    data = private_fixture(tmp_path)
    git(tmp_path, 'init', '-q')
    file = tmp_path / 'example.txt'
    file.write_text(data['api_key'], encoding='utf-8')
    git(tmp_path, 'add', 'example.txt')
    file.write_text('placeholder', encoding='utf-8')
    assert audit.findings(tmp_path, staged=False) == (1, [])
    assert audit.findings(tmp_path, staged=True) == (1, [('example.txt', 'local-credential')])
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['audit_secrets.py', '--staged'])
    assert audit.main()
    output = capsys.readouterr().out
    assert 'example.txt: local-credential' in output and data['api_key'] not in output


def test_invalid_local_audit_configuration_fails_without_input(tmp_path, audit_tools):
    audit, _ = audit_tools
    path = tmp_path / 'runtime/private.json'
    path.parent.mkdir()
    path.write_text('invalid-local-sensitive-input', encoding='utf-8')
    with pytest.raises(ValueError) as caught:
        audit.rules_for(tmp_path)
    assert 'invalid-local-sensitive-input' not in str(caught.value)


@pytest.mark.parametrize('location', ['blob', 'commit'])
def test_github_upload_audits_local_model_values_without_network(tmp_path, audit_tools, location):
    _, sync = audit_tools
    data = private_fixture(tmp_path)
    git(tmp_path, 'init', '-q')
    (tmp_path / 'example.txt').write_text(data['model'] if location == 'blob' else 'placeholder', encoding='utf-8')
    git(tmp_path, 'add', 'example.txt')
    git(tmp_path, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-q',
        '-m', data['model'] if location == 'commit' else 'fixture')
    client = object.__new__(sync.GitHub)
    client.root = tmp_path
    with pytest.raises(ValueError, match='committed secret pattern') as caught:
        client.audit('HEAD')
    assert data['model'] not in str(caught.value)
