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
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit_secrets import FORBIDDEN, rules_for

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
    if len(parents) != 1:
        raise ValueError("only linear existing commits are supported")
    return {"tree":values["tree"].decode(), "parents":parents, "message":message.decode("utf-8"),
            "author":identity(values["author"]), "committer":identity(values["committer"])}


def changes(root, parent, target):
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
    def __init__(self, root):
        self.root = root
        remote = git(root, "remote", "get-url", "origin").decode().strip()
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?", remote)
        if not match:
            raise ValueError("unexpected repository origin")
        self.repo = match[1]
        credentials = git(root, "credential", "fill", data=b"protocol=https\nhost=github.com\n\n")
        values = dict(line.split(b"=", 1) for line in credentials.splitlines() if b"=" in line)
        self._token = values[b"password"].decode()
        account = self.api_request('/user')
        metadata = self.api("")
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
        request = urllib.request.Request("https://api.github.com"+path,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None, method=method,
            headers={"Authorization":"Bearer "+self._token, "Accept":"application/vnd.github+json",
                     "Content-Type":"application/json", "X-GitHub-Api-Version":"2022-11-28"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            # Response bodies/headers are not included in user-visible errors.
            raise ValueError("GitHub API status "+str(error.code)) from None

    def audit(self, target, parent=None):
        rules = rules_for(self.root)
        metadata = git(self.root, 'cat-file', 'commit', target)
        if any(pattern.search(metadata) for pattern in rules.values()):
            raise ValueError('committed secret pattern in commit metadata')
        count = 0
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
            data = git(self.root, "cat-file", "blob", sha.decode())
            if any(pattern.search(data) for pattern in rules.values()):
                raise ValueError("committed secret pattern: "+path)
            count += 1
        return count

    def upload_commit(self, target):
        data = commit_data(self.root, target)
        parent = data["parents"][0]
        base_tree = git(self.root, "rev-parse", parent+"^{tree}").decode().strip()
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
        tree = self.api("/git/trees", method="POST", body={"base_tree":base_tree, "tree":entries})
        if tree["sha"] != data["tree"]:
            raise ValueError("uploaded tree hash mismatch")
        commit = self.api("/git/commits", method="POST", body=data)
        if commit["sha"] != target:
            raise ValueError("uploaded commit hash mismatch; refs were not advanced")

    def sync(self, branch, target):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,180}", branch) or ".." in branch:
            raise ValueError("invalid target branch")
        remote = self.api("/git/ref/heads/"+branch)["object"]["sha"]
        git(self.root, "merge-base", "--is-ancestor", remote, target)
        pending = git(self.root, "rev-list", "--reverse", remote+".."+target).decode().splitlines()
        for sha in pending:
            self.audit(sha, commit_data(self.root, sha)["parents"][0])
        for sha in pending:
            self.upload_commit(sha)
        if remote != target:
            result = self.api("/git/refs/heads/"+branch, method="PATCH", body={"sha":target,"force":False})
            if result["object"]["sha"] != target:
                raise ValueError("remote ref hash mismatch")
        if self.api("/git/ref/heads/"+branch)["object"]["sha"] != target:
            raise ValueError("remote verification failed")
        git(self.root, "update-ref", "refs/remotes/origin/"+branch, target)
        return {"repository":self.repo, "branch":branch, "sha":target, "uploaded_commits":len(pending),
                "force":False, "verified":True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--branch")
    parser.add_argument("--also-main", action="store_true")
    args = parser.parse_args()
    target = git(args.root, "rev-parse", "HEAD").decode().strip()
    branch = args.branch or git(args.root, "symbolic-ref", "--short", "HEAD").decode().strip()
    github = GitHub(args.root)
    for ref in dict.fromkeys([branch, *(["main"] if args.also_main else [])]):
        print(json.dumps(github.sync(ref, target)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError):
        raise SystemExit("GitHub synchronization stopped; no force update or credential output.") from None
