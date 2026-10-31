"""Validate and render the single development-task registry."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs/development/task-registry.json"
LABELS = {"planned": "未开始", "in_progress": "进行中", "blocked": "受阻", "done": "完成", "cancelled": "取消"}


def load():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def render(registry):
    todo = "# TODO · 尚未完成的开发与验收\n\n本视图由 tools/tasks.py 从[任务注册表](docs/development/task-registry.json)生成。已确认方案正在实施；实现与真实验收分别记录。\n"
    for milestone, title in registry["milestones"].items():
        rows = [t for t in registry["tasks"] if t["milestone"] == milestone and t["status"] not in ("done", "cancelled")]
        if not rows:
            continue
        todo += f"\n## {milestone} · {title}\n\n| ID | 类别/状态 | 工作 | 依赖 | 验收摘要 |\n| --- | --- | --- | --- | --- |\n"
        for t in rows:
            kind = {"validation": "验收", "delivery": "交付", "documentation": "资料"}.get(t["kind"], "实现")
            status = LABELS[t["status"]]
            if t.get("implementation_progress") == "code_ready" and t["status"] != "done":
                status += "（代码已落地，验证中）"
            summary = "；".join(t["acceptance"]).replace("|", "／")
            todo += f"| {t['id']} | {kind}·{status} | [{t['title']}]({t['design_refs'][0]}) | {'、'.join(t['depends_on'])} | {summary} |\n"
    todo += "\n自动登录、按指令段位匹配与友人房加入/创建按M9推进；QQ查询保持只读，选场使用独立导航工具。完整视觉输入、历史导入、三麻/地方规则及更广QQ控局另行规划。完成任务转入DONE，实战未验证部分使用独立VAL任务跟踪。\n"
    done = "# 已完成记录\n\n本视图由 tools/tasks.py 生成。完成文档、实现与实战验收具有不同含义，以完成记录的实际证据为准。\n\n| ID | 类别 | 已完成内容 | 记录 |\n| --- | --- | --- | --- |\n"
    for t in registry["tasks"]:
        if t["status"] == "done":
            kind = {"documentation": "资料/文档", "validation": "验收", "delivery": "交付"}.get(t["kind"], "实现")
            done += f"| {t['id']} | {kind} | {t['title']} | [完成记录]({t['completed_record']}) |\n"
    cancelled = [t for t in registry["tasks"] if t["status"] == "cancelled"]
    if cancelled:
        done += "\n已取消（不计成功交付）：" + "、".join(t["id"] for t in cancelled) + "。\n"
    return {"TODO.md": todo, "DONE.md": done}


def validate(registry, check_views=True):
    errors = []
    tasks = {t["id"]: t for t in registry["tasks"]}
    if len(tasks) != len(registry["tasks"]):
        errors.append("duplicate task ids")
    visited, active = set(), set()

    def walk(tid):
        if tid in active:
            errors.append(f"dependency cycle: {tid}")
            return
        if tid in visited:
            return
        active.add(tid)
        for dep in tasks[tid]["depends_on"]:
            if dep not in tasks:
                errors.append(f"missing dependency: {tid}/{dep}")
            else:
                walk(dep)
        active.remove(tid)
        visited.add(tid)

    for tid, task in tasks.items():
        walk(tid)
        if task["status"] not in LABELS or not task["acceptance"]:
            errors.append(f"invalid status/acceptance: {tid}")
        for ref in task["design_refs"]:
            if not (ROOT / ref).is_file():
                errors.append(f"missing design: {tid}/{ref}")
        if task["status"] == "done":
            if not task.get("completed_record") or not (ROOT / task["completed_record"]).is_file() or not task["evidence"]:
                errors.append(f"missing completion evidence: {tid}")
            if any(tasks.get(dep, {}).get("status") != "done" for dep in task["depends_on"]):
                errors.append(f"incomplete dependency of completed task: {tid}")
    if check_views:
        for filename, text in render(registry).items():
            if not (ROOT / filename).is_file() or (ROOT / filename).read_text(encoding="utf-8") != text:
                errors.append(f"stale view: {filename}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "render", "start", "ready", "block", "cancel", "done"))
    parser.add_argument("id", nargs="?")
    parser.add_argument("--record")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--commit", action="append", default=[])
    parser.add_argument("--reason")
    args = parser.parse_args()
    registry = load()
    if args.command in ("start", "ready", "block", "cancel", "done"):
        task = next((t for t in registry["tasks"] if t["id"] == args.id), None)
        if not task:
            parser.error("unknown task")
        if not registry["implementation_gate"]["open"]:
            parser.error("implementation gate is closed")
        if task["status"] in ("done", "cancelled"):
            parser.error("closed tasks cannot be reopened; add a follow-up task")
        task["status"] = {"start": "in_progress", "ready": "in_progress", "block": "blocked", "cancel": "cancelled", "done": "done"}[args.command]
        if args.command == "ready":
            task["implementation_progress"] = "code_ready"
        if args.command in ("block", "cancel"):
            if not args.reason:
                parser.error("blocking/cancellation requires a reason")
            task["status_reason"] = args.reason
        if args.command == "done":
            if not args.record or not args.evidence:
                parser.error("completion requires record and evidence")
            task.update(completed_record=args.record, evidence=args.evidence, commits=args.commit,
                        completed_on=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
        errors = validate(registry, check_views=False)
        if errors:
            parser.error("; ".join(errors))
        REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.command != "check":
        for filename, text in render(registry).items():
            (ROOT / filename).write_text(text, encoding="utf-8", newline="\n")
    errors = validate(registry)
    print(json.dumps({"tasks": len(registry["tasks"]), "errors": errors}, ensure_ascii=False))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
