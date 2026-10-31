import importlib.util
import subprocess
import sys
from pathlib import Path


def test_api_git_objects_preserve_exact_commit_tree_and_file_bytes(tmp_path):
    tools = Path(__file__).resolve().parents[1] / "tools"
    sys.path.insert(0, str(tools))
    try:
        spec = importlib.util.spec_from_file_location("github_sync_fixture", tools / "github_sync.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=tmp_path).decode().strip()
        git("init", "-q")
        git("config", "user.name", "Fixture Developer")
        git("config", "user.email", "fixture@example.invalid")
        (tmp_path / "test.txt").write_bytes(b"before\r\n")
        git("add", ".")
        git("-c", "commit.gpgsign=false", "commit", "-qm", "initial")
        parent = git("rev-parse", "HEAD")
        (tmp_path / "test.txt").unlink()
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested/new.txt").write_bytes("含中文\n".encode())
        git("add", "-A")
        git("-c", "commit.gpgsign=false", "commit", "-qm", "change\n\nMore detail.")
        target = git("rev-parse", "HEAD")
        data = module.commit_data(tmp_path, target)
        assert data["parents"] == [parent]
        assert data["tree"] == git("rev-parse", "HEAD^{tree}")
        assert data["message"] == "change\n\nMore detail.\n"
        assert data["author"]["email"] == "fixture@example.invalid"
        entries = module.changes(tmp_path, parent, target)
        assert {e["path"] for e in entries} == {"test.txt", "nested/new.txt"}
        assert next(e for e in entries if e["path"] == "test.txt")["sha"] is None
        assert module.identity(b"Fixture Developer <fixture@example.invalid> 0 +0800")["date"] == "1970-01-01T08:00:00+08:00"
    finally:
        sys.path.remove(str(tools))
