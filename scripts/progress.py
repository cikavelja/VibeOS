from pathlib import Path
import argparse, json
from collections import Counter
ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "docs/tasks.json"
STATUSES = {"NOT_STARTED", "IN_PROGRESS", "BLOCKED", "IMPLEMENTED", "VERIFICATION_FAILED", "VERIFIED"}
def validate(tasks):
    index = {t["id"]: t for t in tasks}
    if len(index) != len(tasks): raise ValueError("Duplicate task ID")
    if len({t["requirement"] for t in tasks}) != len(tasks): raise ValueError("Duplicate requirement ID")
    seen, active = set(), set()
    def visit(k):
        if k in active: raise ValueError(f"Dependency cycle: {k}")
        if k in seen: return
        active.add(k)
        for d in index[k]["dependencies"]:
            if d not in index: raise ValueError(f"Unknown dependency {d}")
            visit(d)
        active.remove(k); seen.add(k)
    for k,t in index.items():
        visit(k)
        if t["status"] not in STATUSES: raise ValueError(f"Invalid status: {k}")
        if not (ROOT / f"docs/tasks/{k}.md").is_file(): raise ValueError(f"Missing task file: {k}")
        if t["status"] == "VERIFIED":
            if not all(index[d]["status"] == "VERIFIED" for d in t["dependencies"]): raise ValueError(f"Unverified dependency: {k}")
            if not (ROOT / f"docs/verification/{k}.md").is_file(): raise ValueError(f"Missing verification report: {k}")
    return index
def render(tasks,index):
    counts=Counter(t["status"] for t in tasks)
    ready=[t["id"] for t in tasks if t["status"]=="NOT_STARTED" and all(index[d]["status"]=="VERIFIED" for d in t["dependencies"])]
    lines=["# Progress (generated)", "", f"Verified: {counts['VERIFIED']}/{len(tasks)}", "", "Eligible tasks: " + (", ".join(ready) or "None"), "", "| Milestone | Total | Verified |", "|---|---:|---:|"]
    for m in sorted({t["milestone"] for t in tasks}):
        rows=[t for t in tasks if t["milestone"]==m]; lines.append(f"| {m} | {len(rows)} | {sum(t['status']=='VERIFIED' for t in rows)} |")
    trace=["# Requirements traceability (generated)","", "| Requirement | Task | Priority | Status |", "|---|---|---|---|"]
    trace.extend(f"| {t['requirement']} | {t['id']} | {t['priority']} | {t['status']} |" for t in tasks)
    return "\n".join(lines)+"\n", "\n".join(trace)+"\n"
if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--write",action="store_true"); a=p.parse_args()
    tasks=json.loads(TASKS.read_text()); index=validate(tasks); progress,trace=render(tasks,index)
    if a.write:
        (ROOT/"docs/PROGRESS.md").write_text(progress); (ROOT/"docs/REQUIREMENTS_TRACEABILITY.md").write_text(trace)
    print(progress)
