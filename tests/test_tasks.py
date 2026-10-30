"""Registry regressions use a separate workspace, never mutate the real TODO."""
import copy
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("task_tool", Path(__file__).parents[1] / "tools/tasks.py")
task_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_tool)


def test_registry_consumption_and_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(task_tool, "ROOT", tmp_path)
    (tmp_path / "design.md").write_text("design")
    (tmp_path / "completed.md").write_text("actual completion record")
    task = {"id": "DEV-1", "kind": "implementation", "milestone": "M0", "title": "test",
            "status": "in_progress", "acceptance": ["tested"], "depends_on": [], "design_refs": ["design.md"]}
    data = {"milestones": {"M0": "baseline"}, "tasks": [task]}
    assert not task_tool.validate(data, False)
    task["implementation_progress"] = "code_ready"
    assert "代码已落地" in task_tool.render(data)["TODO.md"]
    task.update(status="done", completed_record="completed.md", evidence=["real test"])
    views = task_tool.render(data)
    assert "DEV-1" not in views["TODO.md"] and "DEV-1" in views["DONE.md"]
    for name, content in views.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    assert not task_tool.validate(data)
    task["status"] = "blocked"
    assert any("stale view" in x for x in task_tool.validate(data))
    task["status"] = "cancelled"
    assert "不计成功交付" in task_tool.render(data)["DONE.md"]
    bad = copy.deepcopy(data)
    bad["tasks"].append(copy.deepcopy(task))
    assert "duplicate task ids" in task_tool.validate(bad, False)
    task["depends_on"] = ["DEV-1"]
    assert any("cycle" in x for x in task_tool.validate(data, False))
    task["depends_on"] = ["missing"]
    assert any("missing dependency" in x for x in task_tool.validate(data, False))
