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
}
FORBIDDEN = ("runtime/", "browser-profile/", ".env", "config.local.")


def rules_for(root: Path):
    """Keep actual local model identities out of Git without printing them."""
    private_values = {'local-credential': set(), 'local-provider': set(), 'local-model': set(),
                      'local-model-profile': set()}

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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--staged", action="store_true")
    args = p.parse_args()
    try:
        count, hits = findings(ROOT, staged=args.staged)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print('secret audit stopped; private configuration or Git read failed, details withheld')
        return True
    for name, rule in hits:
        print(f"{name}: {rule}")
    print(f"secret audit: {count} files, {len(hits)} findings")
    return bool(hits)


if __name__ == "__main__":
    raise SystemExit(main())
