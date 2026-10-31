import importlib
import json
import subprocess
from pathlib import Path

import pytest
import httpx

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sync(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'tools'))
    return importlib.import_module('github_sync')


def configuration(root):
    path = root / 'runtime/git-sync.json'
    path.parent.mkdir()
    data = json.loads((ROOT / 'git-sync.example.json').read_text(encoding='utf-8'))
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def test_no_local_configuration_only_uses_origin(tmp_path, sync):
    assert sync.sync_configuration(tmp_path) == ([{'remote':'origin','branch_prefix':''}], False)


def test_failure_reports_safe_status_but_withholds_unknown_input(sync):
    assert sync.safe_failure(ValueError('GitHub API status 403')) == {
        'error_type':'ValueError','detail':'GitHub API status 403'}
    assert sync.safe_failure(ValueError('fixture-private-input'))['detail'] == 'details withheld'


@pytest.mark.parametrize('failure', ['status', 'transport'])
def test_api_failures_withhold_response_and_transport_input(sync, failure):
    client = object.__new__(sync.GitHub)
    client._token = 'fixture-token'
    def handle(request):
        if failure == 'transport':
            raise httpx.ConnectError('fixture-private-input', request=request)
        return httpx.Response(401, json={'error':'fixture-private-input'})
    client._http = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        with pytest.raises((OSError, ValueError)) as caught:
            client.api_request('/user')
        assert 'fixture-private-input' not in str(caught.value)
        if failure == 'status':
            assert str(caught.value) == 'GitHub API status 401'
    finally:
        client.close()


def test_api_reuses_one_client_for_sequential_requests(sync, monkeypatch):
    client = object.__new__(sync.GitHub)
    client._token = 'fixture-token'
    created = []
    transport = httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'ok':True})))
    def factory(**kwargs):
        created.append(kwargs)
        return transport
    monkeypatch.setattr(sync.httpx, 'Client', factory)
    try:
        assert client.api_request('/user') == {'ok':True}
        assert client.api_request('/user') == {'ok':True}
        assert len(created) == 1
    finally:
        client.close()


@pytest.mark.parametrize('change', ['visibility', 'private_prefix', 'duplicate', 'repository_id', 'unknown_key'])
def test_configuration_rejects_unsafe_targets(tmp_path, sync, change):
    path, data = configuration(tmp_path)
    if change == 'visibility':
        data['targets'][1]['visibility'] = 'internal'
    elif change == 'private_prefix':
        data['targets'][1]['branch_prefix'] = ''
    elif change == 'duplicate':
        data['targets'][1]['remote'] = 'origin'
    elif change == 'repository_id':
        data['targets'][1]['repository_id'] = True
    else:
        data['targets'][1]['credential'] = 'fixture-input-must-not-be-printed'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='details withheld') as caught:
        sync.sync_configuration(tmp_path)
    assert 'fixture-input-must-not-be-printed' not in str(caught.value)


def test_upload_supports_root_commit_without_base_tree(tmp_path, sync, monkeypatch):
    git(tmp_path, 'init', '-q')
    (tmp_path / 'sample.txt').write_text('synthetic fixture', encoding='utf-8')
    git(tmp_path, 'add', 'sample.txt')
    git(tmp_path, '-c', 'user.name=Mahjong AI Contributors', '-c', 'user.email=contributors@example.invalid',
        'commit', '-q', '-m', 'root')
    sha = git(tmp_path, 'rev-parse', 'HEAD').decode().strip()
    tree = git(tmp_path, 'rev-parse', 'HEAD^{tree}').decode().strip()
    client = object.__new__(sync.GitHub)
    client.root = tmp_path
    requests = []
    def api(path, *, method, body):
        requests.append((path, body))
        return {'sha':tree if path == '/git/trees' else sha}
    monkeypatch.setattr(client, 'api', api)
    client.upload_commit(sha)
    assert requests[0][0] == '/git/trees' and 'base_tree' not in requests[0][1]
    assert requests[0][1]['tree'][0]['content'] == 'synthetic fixture'
    assert requests[1][1]['parents'] == []
    assert requests[1][1]['author']['email'] == 'contributors@example.invalid'


def test_new_mirror_ref_reuses_existing_target_without_upload(tmp_path, sync, monkeypatch):
    client = object.__new__(sync.GitHub)
    client.root, client.repo, client.remote_name = tmp_path, 'fixture-owner/private-fixture', 'private'
    calls = []
    def api(path, *, method='GET', body=None):
        calls.append((path, method, body))
        if method == 'GET' and path.startswith('/git/ref/') and len(calls) == 1:
            raise ValueError('GitHub API status 404')
        if path.startswith('/git/commits/'):
            return {'sha':'fixture-target'}
        return {'object':{'sha':'fixture-target'}}
    def fake_git(root, *args, **kwargs):
        if args[0] == 'rev-list':
            return b'fixture-target\n'
        return b''
    monkeypatch.setattr(client, 'api', api)
    monkeypatch.setattr(sync, 'git', fake_git)
    monkeypatch.setattr(sync, 'commit_data', lambda *args:{'parents':[]})
    monkeypatch.setattr(client, 'audit', lambda *args:1)
    monkeypatch.setattr(client, 'upload_commit', lambda *args:pytest.fail('existing target uploaded again'))
    result = client.sync('mirror/main', 'fixture-target', create=True)
    assert result['verified'] and result['uploaded_commits'] == 0
    assert any(path == '/git/refs' and body == {'ref':'refs/heads/mirror/main','sha':'fixture-target'}
               for path, method, body in calls if method == 'POST')


def test_divergent_ref_is_rejected_before_upload_or_write(tmp_path, sync, monkeypatch):
    client = object.__new__(sync.GitHub)
    client.root = tmp_path
    calls = []
    def api(path, *, method='GET', body=None):
        calls.append((path, method))
        return {'object':{'sha':'fixture-divergent-head'}}
    def fake_git(root, *args, **kwargs):
        raise subprocess.CalledProcessError(1, ['git', *args])
    monkeypatch.setattr(client, 'api', api)
    monkeypatch.setattr(sync, 'git', fake_git)
    monkeypatch.setattr(client, 'upload_commit', lambda *args:pytest.fail('divergent branch uploaded'))
    with pytest.raises(subprocess.CalledProcessError):
        client.sync('mirror/main', 'fixture-target', create=True)
    assert calls == [('/git/ref/heads/mirror/main', 'GET')]


@pytest.mark.parametrize('wrong', ['repository_id', 'visibility', 'archived'])
def test_all_repositories_are_checked_before_any_write(tmp_path, sync, monkeypatch, wrong):
    configuration(tmp_path)
    writes = []
    class Client:
        def __init__(self, root, remote):
            self.metadata = {'id':1 if remote == 'origin' else 2,
                             'visibility':'public' if remote == 'origin' else 'private', 'archived':False}
            if remote == 'private':
                if wrong == 'repository_id':
                    self.metadata['id'] = 999
                else:
                    self.metadata[wrong] = True if wrong == 'archived' else 'public'
        def sync(self, *args, **kwargs): writes.append(args)
    monkeypatch.setattr(sync, 'GitHub', Client)
    monkeypatch.setattr(sync, 'history_findings', lambda root:(1, []))
    monkeypatch.setattr(sync, 'git', lambda *args, **kwargs:b'fixture-target\n')
    with pytest.raises(ValueError, match='mismatch'):
        sync.synchronize(tmp_path, 'development')
    assert not writes


def test_single_repository_failure_is_not_reported_as_dual_success(tmp_path, sync, monkeypatch):
    configuration(tmp_path)
    calls = []
    class Client:
        def __init__(self, root, remote):
            self.remote = remote
            self.metadata = {'id':1 if remote == 'origin' else 2,
                             'visibility':'public' if remote == 'origin' else 'private'}
        def sync(self, branch, target, **kwargs):
            calls.append((self.remote, branch, target))
            if self.remote == 'origin':
                raise ValueError('fixture error')
            return {'remote':self.remote,'branch':branch,'sha':target,'verified':True}
    monkeypatch.setattr(sync, 'GitHub', Client)
    monkeypatch.setattr(sync, 'history_findings', lambda root:(1, []))
    monkeypatch.setattr(sync, 'git', lambda *args, **kwargs:b'fixture-target\n')
    outcomes = []
    with pytest.raises(ValueError, match='not all configured'):
        sync.synchronize(tmp_path, 'development', on_result=outcomes.append)
    assert outcomes[0]['verified'] is False and all(row['verified'] for row in outcomes[1:])
    assert calls == [('origin','development','fixture-target'),('private','mirror/development','fixture-target'),
                     ('private','mirror/main','fixture-target')]
