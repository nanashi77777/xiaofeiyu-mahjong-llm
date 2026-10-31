"""Scan tracked/staged text; report paths and rule names, never matched values."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "api-key": re.compile(rb"sk-[A-Za-z0-9_-]{24,}"),
    "github-token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal-email": re.compile(rb"[\w.+-]+@(?:qq|163|126|gmail|outlook|hotmail)\.[A-Za-z.]+", re.I),
    "personal-directory": re.compile(rb"[A-Z]:[\\/]+Users[\\/]+[^\\/\s\"']+", re.I),
    "live-match-reference": re.compile(rb"\b\d{6}-[a-f0-9]{8}-[a-f0-9-]{27,}\b"),
}
FORBIDDEN = ("runtime/", "browser-profile/", ".env", "config.local.")


def rules_for(root: Path):
    """Keep actual local model identities out of Git without printing them."""
    private_values = {'local-credential': set(), 'local-provider': set(), 'local-model': set(),
                      'local-model-profile': set(), 'local-private-value': set()}

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'profiles' and isinstance(item, dict):
                    private_values['local-model-profile'].update(name for name in item if name not in ('default', 'example'))
                if isinstance(item, str) and item:
                    if key in ('api_key', 'control_token', 'read_token', 'navigation_token'):
                        private_values['local-credential'].add(item)
                    elif key == 'base_url':
                        host = urlsplit(item).hostname
                        if host and host not in ('localhost', '127.0.0.1', '::1') and not host.endswith(('.invalid', '.example')):
                            private_values['local-provider'].add(host)
                    elif key == 'model' and item != 'your-model-id':
                        private_values['local-model'].add(item)
                    elif key in ('model_provider', 'model_profile', 'provider') and item not in ('default', 'example'):
                        private_values['local-model-profile'].add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    try:
        for relative in ('runtime/private.json', 'runtime/model-profiles.json'):
            path = root / relative
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('private configuration must be an object')
                collect(data)
        policy = root / 'runtime/privacy-values.json'
        if policy.exists():
            data = json.loads(policy.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or not isinstance(data.get('blocked_strings'), list)
                    or any(not isinstance(item, str) or not item for item in data['blocked_strings'])):
                raise ValueError('invalid local privacy policy')
            private_values['local-private-value'].update(data['blocked_strings'])
    except (OSError, ValueError):
        raise ValueError('private audit configuration invalid; details withheld') from None
    return {**RULES, **{name: re.compile(b'|'.join(re.escape(value.encode()) for value in sorted(values)),
                                        re.IGNORECASE if name == 'local-provider' else 0)
                       for name, values in private_values.items() if values}}


def findings(root: Path, *, staged=False):
    rules = rules_for(root)
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if staged else ["git", "ls-files", "-z"]
    files = list(filter(None, subprocess.check_output(command, cwd=root).decode("utf-8").split("\0")))
    hits = []
    for name in files:
        if name.startswith(FORBIDDEN):
            hits.append((name, "runtime-file"))
        try:
            data = subprocess.check_output(["git", "show", ":" + name], cwd=root) if staged else (root / name).read_bytes()
        except (OSError, subprocess.CalledProcessError):
            hits.append((name, "unreadable"))
            continue
        for rule, pattern in rules.items():
            if pattern.search(data):
                hits.append((name, rule))
    return len(files), hits


def history_findings(root: Path, rules=None):
    """Audit every object reachable from refs, including author and committer."""
    rules = rules_for(root) if rules is None else rules
    objects = {}
    for line in subprocess.check_output(['git', 'rev-list', '--objects', '--all'], cwd=root).splitlines():
        oid, _, name = line.partition(b' ')
        objects[oid] = name.decode('utf-8', errors='replace') or '<metadata>'
    if not objects:
        return 0, []
    output = subprocess.run(['git', 'cat-file', '--batch'], cwd=root,
                            input=b'\n'.join(objects) + b'\n', capture_output=True, check=True).stdout
    cursor, hits = 0, []
    for oid, name in objects.items():
        end = output.index(b'\n', cursor)
        _, kind, raw_size = output[cursor:end].split()
        size = int(raw_size)
        data = output[end + 1:end + 1 + size]
        cursor = end + size + 2
        location = oid.decode() + ':' + name
        if kind == b'blob' and name.startswith(FORBIDDEN):
            hits.append((location, 'runtime-file'))
        if kind in (b'commit', b'blob', b'tag'):
            for rule, pattern in rules.items():
                if pattern.search(data):
                    hits.append((location, rule))
        if kind == b'commit':
            for email in re.findall(rb'^(?:author|committer) [^\n]* <([^>]+)>', data, re.M):
                if email != b'contributors@example.invalid':
                    hits.append((location, 'nonanonymous-commit-identity'))
    return len(objects), hits


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--staged", action="store_true")
    p.add_argument('--history', action='store_true')
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--private-root', type=Path)
    args = p.parse_args()
    if args.history and args.staged:
        p.error('--history and --staged are mutually exclusive')
    try:
        count, hits = (history_findings(args.root, rules_for(args.private_root or args.root))
                       if args.history else findings(args.root, staged=args.staged))
    except (OSError, ValueError, subprocess.CalledProcessError):
        print('secret audit stopped; private configuration or Git read failed, details withheld')
        return True
    for name, rule in hits:
        print(f"{name}: {rule}")
    print(f"secret audit: {count} files, {len(hits)} findings")
    return bool(hits)


if __name__ == "__main__":
    raise SystemExit(main())
