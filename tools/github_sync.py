"""Sync exact existing Git commits through GitHub's API when Git HTTPS fails.

Uses Git Credential Manager in memory, checks authenticated ownership, audits committed
files and only advances refs without force. Never changes global proxy settings.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from audit_secrets import FORBIDDEN, history_findings, rules_for


def safe_failure(error):
    message = str(error)
    safe = re.fullmatch(r'GitHub API status \d{3}', message) or message in {
        'configured repository identity, visibility or archive state mismatch',
        'repository authenticated ownership check failed',
        'history privacy audit rejected synchronization',
        'invalid local Git synchronization configuration; details withheld',
        'not all configured repositories synchronized; verified results retained',
        'uploaded blob hash mismatch', 'uploaded tree hash mismatch',
        'uploaded commit hash mismatch; refs were not advanced',
    }
    return {'error_type':type(error).__name__, 'detail':message if safe else 'details withheld'}


def sync_configuration(root):
    path = root / 'runtime/git-sync.json'
    if not path.exists():
        return [{'remote':'origin', 'branch_prefix':''}], False
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or set(data) != {'schema_version','also_main','targets'}
                or data['schema_version'] != 'git-sync.v1' or type(data['also_main']) is not bool
                or not isinstance(data['targets'], list) or not data['targets']):
            raise ValueError('invalid sync configuration')
        names = set()
        for item in data['targets']:
            if (not isinstance(item, dict) or set(item) != {'remote','visibility','repository_id','branch_prefix'}
                    or not isinstance(item['remote'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', item['remote'])
                    or item['remote'] in names or item['visibility'] not in ('public','private')
                    or type(item['repository_id']) is not int or item['repository_id'] <= 0
                    or not isinstance(item['branch_prefix'], str)
                    or (item['branch_prefix'] and (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_/-]*/',item['branch_prefix'])
                                                  or '..' in item['branch_prefix'] or '//' in item['branch_prefix']))):
                raise ValueError('invalid sync target')
            if item['visibility'] == 'private' and not item['branch_prefix']:
                raise ValueError('private mirror requires a separate branch namespace')
            names.add(item['remote'])
        return data['targets'], data['also_main']
    except (OSError, ValueError, TypeError):
        raise ValueError('invalid local Git synchronization configuration; details withheld') from None

def git(root, *args, data=None):
    return subprocess.run(["git", *args], cwd=root, input=data, capture_output=True, check=True).stdout


def identity(raw):
    match = re.fullmatch(rb"(.*) <([^<>]+)> (\d+) ([+-])(\d{2})(\d{2})", raw)
    if not match:
        raise ValueError("unsupported Git identity")
    name, email, stamp, sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes)) * (1 if sign == b"+" else -1)
    return {"name":name.decode(), "email":email.decode(),
            "date":datetime.fromtimestamp(int(stamp), timezone(offset)).isoformat()}


def commit_data(root, sha):
    headers, message = git(root, "cat-file", "commit", sha).split(b"\n\n", 1)
    values = {}
    parents = []
    for line in headers.splitlines():
        key, value = line.split(b" ", 1)
        if key == b"parent":
            parents.append(value.decode())
        elif key in (b"tree", b"author", b"committer"):
            values[key.decode()] = value
        else:
            raise ValueError("signed or extended commits require regular Git transport")
    if len(parents) > 1:
        raise ValueError("only linear existing commits are supported")
    return {"tree":values["tree"].decode(), "parents":parents, "message":message.decode("utf-8"),
            "author":identity(values["author"]), "committer":identity(values["committer"])}


def changes(root, parent, target):
    if parent is None:
        entries = []
        for row in git(root, 'ls-tree', '-rz', target).split(b'\0'):
            if not row:
                continue
            meta, path = row.split(b'\t', 1)
            mode, kind, sha = meta.split()
            if kind != b'blob':
                raise ValueError('unsupported root entry')
            entries.append({'path':path.decode(), 'mode':mode.decode(), 'type':'blob', 'sha':sha.decode()})
        return entries
    fields = git(root, "diff-tree", "--no-commit-id", "--raw", "-r", "-z", "--no-renames", parent, target).split(b"\0")
    entries = []
    for index in range(0, len(fields)-1, 2):
        _, mode, _, sha, status = fields[index].split()
        path = fields[index+1].decode("utf-8")
        if mode == b"160000":
            raise ValueError("submodules require regular Git transport")
        entries.append({"path":path, "mode":mode.decode() if status != b"D" else "100644",
                        "type":"blob", "sha":None if status == b"D" else sha.decode()})
    return entries


class GitHub:
    def __init__(self, root, remote_name='origin'):
        self.root = root
        self.remote_name = remote_name
        remote = git(root, "remote", "get-url", remote_name).decode().strip()
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?", remote)
        if not match:
            raise ValueError("unexpected repository origin")
        self.repo = match[1]
        credentials = git(root, "credential", "fill", data=b"protocol=https\nhost=github.com\n\n")
        values = dict(line.split(b"=", 1) for line in credentials.splitlines() if b"=" in line)
        self._token = values[b"password"].decode()
        account = self.api_request('/user')
        metadata = self.api("")
        self.metadata = metadata
        if (metadata.get("full_name") != self.repo
                or metadata.get('owner', {}).get('login') != account.get('login')
                or self.repo.split('/')[0] != account.get('login')
                or not metadata.get('permissions', {}).get('admin')
                or metadata.get('visibility') not in ('private', 'public')):
            raise ValueError("repository authenticated ownership check failed")

    def api(self, path, *, method="GET", body=None):
        return self.api_request('/repos/' + self.repo + path, method=method, body=body)

    def api_request(self, path, *, method="GET", body=None):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('invalid GitHub API path')
        if method != 'GET':
            delay = .9 - (time.monotonic() - getattr(self, '_last_write_mono', 0))
            if delay > 0:
                time.sleep(delay)
            self._last_write_mono = time.monotonic()
        if not getattr(self, '_http', None):
            self._http = httpx.Client(timeout=30, follow_redirects=True)
        try:
            response = self._http.request(method, "https://api.github.com"+path, json=body,
                headers={"Authorization":"Bearer "+self._token, "Accept":"application/vnd.github+json",
                         "Content-Type":"application/json", "X-GitHub-Api-Version":"2022-11-28"})
        except httpx.HTTPError:
            raise OSError('GitHub API transport failed; details withheld') from None
        if response.status_code >= 400:
            # Response bodies/headers are not included in user-visible errors.
            raise ValueError("GitHub API status "+str(response.status_code)) from None
        return response.json() if response.content else None

    def close(self):
        if getattr(self, '_http', None):
            self._http.close()

    def audit(self, target, parent=None):
        rules = rules_for(self.root)
        metadata = git(self.root, 'cat-file', 'commit', target)
        if any(pattern.search(metadata) for pattern in rules.values()):
            raise ValueError('committed secret pattern in commit metadata')
        selected = []
        uploaded = {e["sha"] for e in changes(self.root, parent, target) if e["sha"]} if parent else None
        for record in git(self.root, "ls-tree", "-rz", target).split(b"\0"):
            if not record:
                continue
            meta, raw_path = record.split(b"\t", 1)
            _, kind, sha = meta.split()
            if uploaded is not None and sha.decode() not in uploaded:
                continue  # Existing ancestor objects are already on this remote.
            path = raw_path.decode()
            if path.startswith(FORBIDDEN) or kind != b"blob":
                raise ValueError("committed runtime or unsupported entry: "+path)
            selected.append((sha,path))
        if not selected:
            return 0
        output = git(self.root, 'cat-file', '--batch', data=b'\n'.join(sha for sha,_ in selected)+b'\n')
        cursor = 0
        for sha, path in selected:
            end = output.index(b'\n',cursor)
            fields = output[cursor:end].split()
            if len(fields) != 3 or fields[0] != sha or fields[1] != b'blob':
                raise ValueError('unreadable committed object')
            size = int(fields[2])
            data = output[end+1:end+1+size]
            if len(data) != size:
                raise ValueError('truncated committed object')
            cursor = end+size+2
            if any(pattern.search(data) for pattern in rules.values()):
                raise ValueError("committed secret pattern: "+path)
        return len(selected)

    def upload_commit(self, target):
        data = commit_data(self.root, target)
        parent = data["parents"][0] if data['parents'] else None
        entries = changes(self.root, parent, target)
        for entry in entries:
            if entry["sha"] is None:
                continue
            content = git(self.root, "cat-file", "blob", entry["sha"])
            try:
                entry["content"] = content.decode("utf-8")
                del entry["sha"]
            except UnicodeDecodeError:
                blob = self.api("/git/blobs", method="POST", body={"content":base64.b64encode(content).decode(), "encoding":"base64"})
                if blob["sha"] != entry["sha"]:
                    raise ValueError("uploaded blob hash mismatch") from None
        tree_body = {'tree':entries}
        if parent:
            tree_body['base_tree'] = git(self.root, "rev-parse", parent+"^{tree}").decode().strip()
        tree = self.api("/git/trees", method="POST", body=tree_body)
        if tree["sha"] != data["tree"]:
            raise ValueError("uploaded tree hash mismatch")
        commit = self.api("/git/commits", method="POST", body=data)
        if commit["sha"] != target:
            raise ValueError("uploaded commit hash mismatch; refs were not advanced")

    def remote_commit_exists(self, target):
        try:
            return self.api('/git/commits/'+target)['sha'] == target
        except ValueError as error:
            if str(error) in ('GitHub API status 404', 'GitHub API status 409'):
                return False
            raise

    def sync(self, branch, target, *, create=False, on_progress=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,180}", branch) or ".." in branch:
            raise ValueError("invalid target branch")
        try:
            remote = self.api("/git/ref/heads/"+branch)["object"]["sha"]
        except ValueError as error:
            if not create or str(error) != 'GitHub API status 404':
                raise
            remote = None
        if remote:
            git(self.root, "merge-base", "--is-ancestor", remote, target)
        pending = git(self.root, "rev-list", "--reverse", remote+".."+target if remote else target).decode().splitlines()
        for sha in pending:
            parents = commit_data(self.root, sha)['parents']
            self.audit(sha, parents[0] if parents else None)
        uploaded = 0
        if pending and not self.remote_commit_exists(target):
            for sha in pending:
                if not self.remote_commit_exists(sha):
                    self.upload_commit(sha)
                    uploaded += 1
                    if on_progress and (uploaded % 5 == 0 or sha == pending[-1]):
                        on_progress({'remote':getattr(self,'remote_name','origin'), 'status':'uploading',
                                     'uploaded_commits':uploaded, 'pending_commits':len(pending)})
        if remote is None:
            result = self.api('/git/refs', method='POST', body={'ref':'refs/heads/'+branch,'sha':target})
            if result['object']['sha'] != target:
                raise ValueError('created remote ref hash mismatch')
        elif remote != target:
            result = self.api("/git/refs/heads/"+branch, method="PATCH", body={"sha":target,"force":False})
            if result["object"]["sha"] != target:
                raise ValueError("remote ref hash mismatch")
        if self.api("/git/ref/heads/"+branch)["object"]["sha"] != target:
            raise ValueError("remote verification failed")
        git(self.root, "update-ref", "refs/remotes/"+getattr(self,'remote_name','origin')+"/"+branch, target)
        return {"repository":self.repo, "branch":branch, "sha":target, "uploaded_commits":uploaded,
                "force":False, "verified":True}


def synchronize(root, branch, *, also_main=False, on_result=None):
    targets, configured_main = sync_configuration(root)
    _, findings = history_findings(root)
    if findings:
        raise ValueError('history privacy audit rejected synchronization')
    target = git(root, 'rev-parse', 'HEAD').decode().strip()
    branches = list(dict.fromkeys([branch, *(['main'] if also_main or configured_main else [])]))
    clients = []
    # Validate every configured repository before any upload/ref write.
    for item in targets:
        client = GitHub(root, item['remote'])
        if (client.metadata.get('archived') or
                ('visibility' in item and client.metadata.get('visibility') != item['visibility']) or
                ('repository_id' in item and client.metadata.get('id') != item['repository_id'])):
            raise ValueError('configured repository identity, visibility or archive state mismatch')
        clients.append((client,item))
    results, failed = [], False
    for client, item in clients:
        for source in branches:
            destination = item['branch_prefix'] + source
            try:
                result = client.sync(destination, target, create=bool(item['branch_prefix']), on_progress=on_result)
                results.append(result)
                if on_result:
                    on_result(result)
            except (ValueError, OSError, subprocess.CalledProcessError) as error:
                failed = True
                if on_result:
                    on_result({'remote':item['remote'], 'branch':destination, 'verified':False,
                               'error':'synchronization failed', **safe_failure(error)})
                break
        if getattr(client, 'close', None):
            client.close()
    if failed:
        raise ValueError('not all configured repositories synchronized; verified results retained')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--branch")
    parser.add_argument("--also-main", action="store_true")
    args = parser.parse_args()
    branch = args.branch or git(args.root, "symbolic-ref", "--short", "HEAD").decode().strip()
    synchronize(args.root, branch, also_main=args.also_main,
                on_result=lambda result:print(json.dumps(result),flush=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({'status':'incomplete', **safe_failure(error)}),flush=True)
        raise SystemExit("GitHub synchronization incomplete; inspect verified results above. No force update or credential output.") from None
