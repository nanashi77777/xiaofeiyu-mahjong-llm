"""Scan tracked/staged text; report paths and rule names, never matched values."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "api-key": re.compile(rb"sk-[A-Za-z0-9]{24,}"),
    "github-token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{24,}"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
FORBIDDEN = ("runtime/", "browser-profile/", ".env", "config.local.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--staged", action="store_true")
    args = p.parse_args()
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if args.staged else ["git", "ls-files", "-z"]
    files = subprocess.check_output(command, cwd=ROOT).decode("utf-8").split("\0")
    hits = []
    for name in filter(None, files):
        if name.startswith(FORBIDDEN):
            hits.append((name, "runtime-file"))
        try:
            data = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT) if args.staged else (ROOT / name).read_bytes()
        except (OSError, subprocess.CalledProcessError):
            hits.append((name, "unreadable"))
            continue
        for rule, pattern in RULES.items():
            if pattern.search(data):
                hits.append((name, rule))
    for name, rule in hits:
        print(f"{name}: {rule}")
    print(f"secret audit: {len(list(filter(None, files)))} files, {len(hits)} findings")
    return bool(hits)


if __name__ == "__main__":
    raise SystemExit(main())
