import importlib
import json
import subprocess
from pathlib import Path

import pytest
import httpx

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'tools'))
    return importlib.import_module('audit_secrets')


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def commit(root, message, *, mail='contributors@example.invalid'):
    git(root, 'add', 'sample.txt')
    git(root, '-c', 'user.name=Mahjong AI Contributors', '-c', 'user.email='+mail,
        'commit', '-q', '-m', message)


def test_history_audit_rejects_removed_secret_and_nonanonymous_author(tmp_path, audit):
    git(tmp_path, 'init', '-q')
    policy = tmp_path / 'runtime/privacy-values.json'
    policy.parent.mkdir()
    policy.write_text(json.dumps({'blocked_strings':['fixture-history-only-private-value']}), encoding='utf-8')
    sample = tmp_path / 'sample.txt'
    sample.write_text('fixture-history-only-private-value', encoding='utf-8')
    commit(tmp_path, 'initial', mail='fixture-person@example.net')
    sample.write_text('placeholder', encoding='utf-8')
    commit(tmp_path, 'remove private text')
    assert audit.findings(tmp_path) == (1, [])
    count, hits = audit.history_findings(tmp_path)
    assert count > 2
    assert any(rule == 'local-private-value' for _, rule in hits)
    assert any(rule == 'nonanonymous-commit-identity' for _, rule in hits)
    assert all('fixture-history-only-private-value' not in path for path, _ in hits)


def test_history_audit_accepts_anonymous_clean_history(tmp_path, audit):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'sample.txt').write_text('placeholder', encoding='utf-8')
    commit(tmp_path, 'initial')
    count, hits = audit.history_findings(tmp_path)
    assert count == 3 and not hits


@pytest.mark.parametrize('public', [True, False])
def test_sync_checks_authenticated_owner_for_public_and_private(tmp_path, monkeypatch, public):
    monkeypatch.syspath_prepend(str(ROOT / 'tools'))
    sync = importlib.import_module('github_sync')
    def fake_git(root, *args, **kwargs):
        if args[:2] == ('remote', 'get-url'):
            return b'https://github.com/fixture-owner/fixture-project.git\n'
        return b'username=fixture-owner\npassword=fixture-test-token\n'
    def fake_api(self, path, **kwargs):
        if path == '/user':
            return {'login':'fixture-owner'}
        return {'full_name':'fixture-owner/fixture-project','owner':{'login':'fixture-owner'},
                'visibility':'public' if public else 'private','permissions':{'admin':True}}
    monkeypatch.setattr(sync, 'git', fake_git)
    monkeypatch.setattr(sync.GitHub, 'api_request', fake_api)
    assert sync.GitHub(tmp_path).repo == 'fixture-owner/fixture-project'
    monkeypatch.setattr(sync.GitHub, 'api_request', lambda self, path, **kwargs:
                        {'login':'someone-else'} if path == '/user' else fake_api(self, path, **kwargs))
    with pytest.raises(ValueError, match='ownership check failed'):
        sync.GitHub(tmp_path)


def test_local_privacy_policy_fails_closed(tmp_path, audit):
    path = tmp_path / 'runtime/privacy-values.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'blocked_strings':[None]}), encoding='utf-8')
    with pytest.raises(ValueError, match='details withheld'):
        audit.rules_for(tmp_path)


def test_github_settings_accept_no_content_response(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'tools'))
    sync = importlib.import_module('github_sync')
    client = object.__new__(sync.GitHub)
    client._token = 'fixture-token'
    client._http = httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(204)))
    try:
        assert client.api_request('/repos/fixture-owner/fixture-project/actions/permissions',
                                  method='PUT', body={'enabled':False}) is None
    finally:
        client.close()
