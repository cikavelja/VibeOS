from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, NoReturn, TypedDict

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "docs/tasks.json"

MAX_REGISTRY_BYTES = 1_048_576
MAX_DOC_BYTES = 524_288

STATUSES: frozenset[str] = frozenset(
    {
        "NOT_STARTED",
        "IN_PROGRESS",
        "BLOCKED",
        "IMPLEMENTED",
        "VERIFICATION_FAILED",
        "VERIFIED",
    }
)
PRIORITIES: frozenset[str] = frozenset({"MVP", "POST_MVP"})
DEPENDENCY_GATED_STATUSES: frozenset[str] = frozenset(
    {"IN_PROGRESS", "IMPLEMENTED", "VERIFICATION_FAILED", "VERIFIED"}
)
REQUIRED_TASK_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "milestone",
        "title",
        "requirement",
        "acceptance",
        "dependencies",
        "status",
        "priority",
    }
)
MANDATORY_CRITERIA: tuple[str, ...] = ("AC-01", "AC-02", "AC-03")
REQUIRED_VERIFICATION_SECTIONS: tuple[str, ...] = (
    "## Commands and exit codes",
    "## CI run and commit",
    "## Findings and severity",
    "## Limitations and blockers",
    "## Human approval",
)
POSITIVE_EVIDENCE_SECTIONS: frozenset[str] = frozenset(
    {
        "## Commands and exit codes",
        "## CI run and commit",
        "## Human approval",
    }
)
REQUIRED_METADATA_KEYS: frozenset[str] = frozenset({"Verdict", "Commit", "Reviewer"})

TASK_ID_RE = re.compile(r"^M(?P<milestone>\d{2})-T(?!00)(?P<number>\d{2})$")
MILESTONE_ID_RE = re.compile(r"^M(?P<milestone>\d{2})$")
REQUIREMENT_ID_RE = re.compile(r"^REQ-(?P<milestone>\d{2})-(?!00)(?P<number>\d{2})$")
VERIFICATION_TITLE_RE = re.compile(r"^#\s+Verification\s+(?:—|--|-)\s+(?P<id>\S+)\s*$")
COMMIT_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")
CRITERION_ID_RE = re.compile(r"^AC-\d{2,}$")
TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
FENCE_RE = re.compile(r"^(?:`{3,}|~{3,})")
HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
UNAPPROVED_HUMAN_RE = re.compile(
    r"\b(?:not\s+approved|unapproved)\b"
    r"|(?:pending|awaiting)\s+(?:human\s+)?(?:approval|review)",
    re.IGNORECASE,
)
FORBIDDEN_EVIDENCE_TOKEN_RE = re.compile(r"\b(?:TBD|NOT_RUN)\b")

PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {
        "",
        "tbd",
        "todo",
        "pending",
        "not_run",
        "not run",
        "wip",
        "fixme",
        "xxx",
        "-",
        "--",
        "---",
        "...",
        "task-id",
    }
)
EMPTY_EVIDENCE_VALUES: frozenset[str] = PLACEHOLDER_VALUES | frozenset(
    {"none", "n/a", "na", "nil", "null"}
)


class TaskEntry(TypedDict):
    id: str
    milestone: str
    title: str
    requirement: str
    acceptance: str
    dependencies: list[str]
    status: str
    priority: str


class RegistryValidationError(ValueError):
    """Raised when task registry or verification evidence violates contracts."""


class _CLIUsageError(ValueError):
    """Raised when CLI arguments are invalid."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _CLIUsageError(message)


@dataclass(frozen=True)
class ProgressSummary:
    total: int
    verified: int
    eligible: tuple[str, ...]
    progress_markdown: str
    traceability_markdown: str


def _normalize_placeholder_candidate(text: str) -> str:
    stripped = HTML_COMMENT_RE.sub("", text).strip()
    cleaned = re.sub(r"^[`*_~\s]+|[`*_~.\s!?:;]+$", "", stripped)
    return cleaned.strip().lower()


def _validate_root_dir(root: Path) -> Path:
    if not isinstance(root, Path):
        raise RegistryValidationError("Repository root must be a pathlib.Path")
    if root.is_symlink():
        raise RegistryValidationError(f"Repository root must not be a symlink: {root}")
    if not root.exists() or not root.is_dir():
        raise RegistryValidationError(
            f"Repository root directory does not exist: {root}"
        )
    return root


def _require_safe_parent_dirs(path: Path, root: Path, label: str) -> None:
    root_resolved = root.resolve()
    try:
        rel_parts = path.parent.relative_to(root).parts
        cursor = root
        for part in rel_parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise RegistryValidationError(
                    f"Parent directory for {label} must not be a symlink: {cursor}"
                )
    except ValueError as exc:
        raise RegistryValidationError(
            f"{label} escapes repository root: {path}"
        ) from exc

    try:
        path.parent.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise RegistryValidationError(
            f"{label} escapes repository root: {path}"
        ) from exc


def _require_regular_file(
    path: Path,
    root: Path,
    label: str,
    *,
    max_bytes: int,
    missing_message: str | None = None,
) -> Path:
    _require_safe_parent_dirs(path, root, label)
    if path.is_symlink():
        raise RegistryValidationError(f"{label} must not be a symlink: {path}")
    if not path.exists() or not path.is_file():
        raise RegistryValidationError(
            missing_message or f"Missing required {label}: {path}"
        )
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RegistryValidationError(
            f"{label} escapes repository root: {path}"
        ) from exc
    size = path.stat().st_size
    if size > max_bytes:
        raise RegistryValidationError(
            f"{label} exceeds maximum size ({size} > {max_bytes} bytes)"
        )
    return path


def _require_safe_output_path(path: Path, root: Path, label: str) -> Path:
    _require_safe_parent_dirs(path, root, label)
    if not path.parent.exists() or not path.parent.is_dir():
        raise RegistryValidationError(
            f"Missing parent directory for {label}: {path.parent}"
        )
    if path.is_symlink():
        raise RegistryValidationError(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise RegistryValidationError(f"{label} must be a regular file path: {path}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RegistryValidationError(
            f"{label} escapes repository root: {path}"
        ) from exc
    return path


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryValidationError(f"{label} must be valid UTF-8") from exc
    except OSError as exc:
        raise RegistryValidationError(f"Unable to read {label}: {exc}") from exc


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise RegistryValidationError(
                f"Duplicate JSON key {key!r} in task registry"
            )
        obj[key] = value
    return obj


def _reject_json_constants(token: str) -> NoReturn:
    raise RegistryValidationError(
        f"Invalid JSON numeric constant {token!r} in task registry"
    )


def parse_tasks_json(
    raw_json: str, label: str = "docs/tasks.json"
) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(
            raw_json,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constants,
        )
    except json.JSONDecodeError as exc:
        raise RegistryValidationError(f"Malformed JSON in {label}: {exc}") from exc

    if not isinstance(parsed, list) or not parsed:
        raise RegistryValidationError(f"{label} root must be a non-empty JSON array")
    for idx, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            raise RegistryValidationError(
                f"{label} entry [{idx}] must be a JSON object"
            )
    return parsed


def load_tasks_file(root: Path = ROOT) -> list[dict[str, Any]]:
    checked_root = _validate_root_dir(root)
    tasks_path = _require_regular_file(
        checked_root / "docs" / "tasks.json",
        checked_root,
        "docs/tasks.json",
        max_bytes=MAX_REGISTRY_BYTES,
    )
    return parse_tasks_json(
        _read_utf8(tasks_path, "docs/tasks.json"), "docs/tasks.json"
    )


def _split_markdown_row(line: str, expected_cols: int | None = None) -> list[str]:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")) or len(stripped) < 2:
        raise RegistryValidationError(f"Malformed Markdown table row: {line!r}")
    inner = stripped[1:-1]
    if expected_cols is not None and expected_cols >= 1:
        parts = re.split(r"(?<!\\)\|", inner, maxsplit=expected_cols - 1)
    else:
        parts = re.split(r"(?<!\\)\|", inner)
    return [part.strip() for part in parts]


def _validate_verification_metadata(
    preamble_lines: Sequence[str], task_id: str
) -> None:
    verdict_lines: list[str] = []
    for raw_line in preamble_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        if stripped.startswith("Verdict:"):
            verdict_lines.append(stripped)
        else:
            raise RegistryValidationError(
                f"Unexpected preamble content in verification report {task_id}: {stripped!r}"
            )
    if len(verdict_lines) != 1:
        raise RegistryValidationError(
            f"Verification report {task_id} must declare exactly one 'Verdict:' metadata line"
        )

    metadata: dict[str, str] = {}
    for segment in verdict_lines[0].split("|"):
        part = segment.strip()
        if ":" not in part:
            raise RegistryValidationError(
                f"Malformed metadata segment in verification report {task_id}: {part!r}"
            )
        key_raw, val_raw = part.split(":", 1)
        key = key_raw.strip()
        val = val_raw.strip()
        if key in metadata:
            raise RegistryValidationError(
                f"Duplicate metadata key {key!r} in verification report {task_id}"
            )
        metadata[key] = val

    missing_keys = REQUIRED_METADATA_KEYS - set(metadata.keys())
    unknown_keys = set(metadata.keys()) - REQUIRED_METADATA_KEYS
    if missing_keys or unknown_keys:
        raise RegistryValidationError(
            f"Verification report {task_id} metadata must contain exactly "
            f"{sorted(REQUIRED_METADATA_KEYS)}, got {sorted(metadata.keys())}"
        )

    verdict = metadata["Verdict"]
    if verdict != "VERIFIED":
        raise RegistryValidationError(
            f"Verification report {task_id} must have 'Verdict: VERIFIED', got {verdict!r}"
        )

    commit_raw = metadata["Commit"]
    if commit_raw.startswith("`") or commit_raw.endswith("`"):
        if not (
            len(commit_raw) >= 2
            and commit_raw.startswith("`")
            and commit_raw.endswith("`")
        ):
            raise RegistryValidationError(
                f"Verification report {task_id} has unbalanced backticks in Commit: {commit_raw!r}"
            )
        commit_val = commit_raw[1:-1].strip()
    else:
        commit_val = commit_raw

    if COMMIT_HASH_RE.fullmatch(commit_val) is None or not any(
        ch != "0" for ch in commit_val
    ):
        raise RegistryValidationError(
            f"Verification report {task_id} must record a valid lowercase hexadecimal "
            f"commit hash (not placeholder), got {commit_raw!r}"
        )

    reviewer_norm = _normalize_placeholder_candidate(metadata["Reviewer"])
    if reviewer_norm in EMPTY_EVIDENCE_VALUES:
        raise RegistryValidationError(
            f"Verification report {task_id} must record a non-placeholder Reviewer"
        )


def _validate_verification_criteria_table(
    preamble_lines: Sequence[str], task_id: str
) -> None:
    table_lines = [
        line.strip() for line in preamble_lines if line.strip().startswith("|")
    ]
    if len(table_lines) < 3:
        raise RegistryValidationError(
            f"Verification report {task_id} is missing a complete acceptance criteria table"
        )

    header_cells = _split_markdown_row(table_lines[0])
    if header_cells != ["Criterion", "PASS/FAIL/NOT_RUN", "Evidence"]:
        raise RegistryValidationError(
            f"Verification report {task_id} has invalid criteria table header: {table_lines[0]!r}"
        )

    separator_cells = _split_markdown_row(table_lines[1])
    if len(separator_cells) != 3 or not all(
        TABLE_SEPARATOR_CELL_RE.fullmatch(cell) is not None for cell in separator_cells
    ):
        raise RegistryValidationError(
            f"Verification report {task_id} has invalid criteria table separator: {table_lines[1]!r}"
        )

    criteria_status: dict[str, str] = {}
    for row_line in table_lines[2:]:
        cells = _split_markdown_row(row_line, expected_cols=3)
        if len(cells) != 3:
            raise RegistryValidationError(
                f"Verification report {task_id} has malformed criterion row: {row_line!r}"
            )
        criterion_id, status_cell, evidence_cell = cells
        if CRITERION_ID_RE.fullmatch(criterion_id) is None:
            raise RegistryValidationError(
                f"Verification report {task_id} has invalid criterion ID {criterion_id!r}"
            )
        if criterion_id in criteria_status:
            raise RegistryValidationError(
                f"Verification report {task_id} contains duplicate criterion {criterion_id}"
            )
        if status_cell != "PASS":
            raise RegistryValidationError(
                f"Verification report {task_id} criterion {criterion_id} must be 'PASS', got {status_cell!r}"
            )
        evidence_norm = _normalize_placeholder_candidate(evidence_cell)
        if evidence_norm in EMPTY_EVIDENCE_VALUES:
            raise RegistryValidationError(
                f"Verification report {task_id} criterion {criterion_id} is missing substantive evidence"
            )
        criteria_status[criterion_id] = status_cell

    for mandatory_id in MANDATORY_CRITERIA:
        if mandatory_id not in criteria_status:
            raise RegistryValidationError(
                f"Verification report {task_id} is missing mandatory criterion {mandatory_id}"
            )


def validate_verification_report(report_path: Path, root: Path, task_id: str) -> None:
    _require_regular_file(
        report_path,
        root,
        f"docs/verification/{task_id}.md",
        max_bytes=MAX_DOC_BYTES,
        missing_message=f"Missing verification report: {task_id}",
    )
    content = _read_utf8(report_path, f"docs/verification/{task_id}.md")
    lines = content.splitlines()

    first_non_empty_idx: int | None = None
    for idx, raw_line in enumerate(lines):
        if raw_line.strip():
            first_non_empty_idx = idx
            break
    if first_non_empty_idx is None:
        raise RegistryValidationError(
            f"Verification report {task_id} must not be empty"
        )

    title_match = VERIFICATION_TITLE_RE.fullmatch(lines[first_non_empty_idx].strip())
    if title_match is None or title_match.group("id") != task_id:
        raise RegistryValidationError(
            f"Verification report {task_id} must start with '# Verification — {task_id}', "
            f"got {lines[first_non_empty_idx].strip()!r}"
        )

    preamble_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    in_fence = False

    for raw_line in lines[first_non_empty_idx + 1 :]:
        stripped = raw_line.strip()
        if FENCE_RE.match(stripped) is not None:
            in_fence = not in_fence
            if current_section is None:
                preamble_lines.append(raw_line)
            else:
                sections[current_section].append(raw_line)
            continue

        if not in_fence and stripped.startswith("# ") and not stripped.startswith("##"):
            raise RegistryValidationError(
                f"Verification report {task_id} contains unexpected top-level heading: {stripped!r}"
            )

        if (
            not in_fence
            and stripped.startswith("## ")
            and not stripped.startswith("###")
        ):
            if stripped in sections:
                raise RegistryValidationError(
                    f"Verification report {task_id} contains duplicate section {stripped!r}"
                )
            current_section = stripped
            sections[current_section] = []
            continue

        if current_section is None:
            preamble_lines.append(raw_line)
        else:
            sections[current_section].append(raw_line)

    if in_fence:
        raise RegistryValidationError(
            f"Verification report {task_id} contains an unclosed Markdown code fence"
        )

    _validate_verification_metadata(preamble_lines, task_id)
    _validate_verification_criteria_table(preamble_lines, task_id)

    for required_heading in REQUIRED_VERIFICATION_SECTIONS:
        if required_heading not in sections:
            raise RegistryValidationError(
                f"Verification report {task_id} is missing required section {required_heading!r}"
            )
        raw_body = "\n".join(sections[required_heading])
        cleaned_body = HTML_COMMENT_RE.sub("", raw_body).strip()
        norm_body = _normalize_placeholder_candidate(cleaned_body)

        if required_heading in POSITIVE_EVIDENCE_SECTIONS:
            if norm_body in EMPTY_EVIDENCE_VALUES:
                raise RegistryValidationError(
                    f"Verification report {task_id} section {required_heading!r} must be non-empty and non-placeholder"
                )
            if FORBIDDEN_EVIDENCE_TOKEN_RE.search(cleaned_body) is not None:
                raise RegistryValidationError(
                    f"Verification report {task_id} section {required_heading!r} contains unresolved placeholder token"
                )
            if (
                required_heading == "## Human approval"
                and UNAPPROVED_HUMAN_RE.search(cleaned_body) is not None
            ):
                raise RegistryValidationError(
                    f"Verification report {task_id} section '## Human approval' must record completed approval"
                )
        else:
            if norm_body in PLACEHOLDER_VALUES or re.search(r"\bTBD\b", cleaned_body):
                raise RegistryValidationError(
                    f"Verification report {task_id} section {required_heading!r} must be non-empty and non-placeholder"
                )


def _validate_task_schema(raw_task: object, idx: int) -> TaskEntry:
    if not isinstance(raw_task, dict):
        raise RegistryValidationError(f"Task entry [{idx}] must be a mapping")

    actual_keys = set(raw_task.keys())
    missing = REQUIRED_TASK_KEYS - actual_keys
    if missing:
        raise RegistryValidationError(
            f"Task entry [{idx}] is missing required keys: {sorted(missing)}"
        )
    unknown = actual_keys - REQUIRED_TASK_KEYS
    if unknown:
        raise RegistryValidationError(
            f"Task entry [{idx}] contains unknown keys: {sorted(unknown)}"
        )

    task_id = raw_task["id"]
    if not isinstance(task_id, str):
        raise RegistryValidationError(f"Task entry [{idx}] 'id' must be a string")
    id_match = TASK_ID_RE.fullmatch(task_id)
    if id_match is None:
        raise RegistryValidationError(f"Invalid task ID format at [{idx}]: {task_id!r}")

    milestone = raw_task["milestone"]
    if not isinstance(milestone, str):
        raise RegistryValidationError(f"Task {task_id} 'milestone' must be a string")
    ms_match = MILESTONE_ID_RE.fullmatch(milestone)
    if ms_match is None or ms_match.group("milestone") != id_match.group("milestone"):
        raise RegistryValidationError(
            f"Invalid or mismatched milestone for {task_id}: {milestone!r}"
        )

    requirement = raw_task["requirement"]
    if not isinstance(requirement, str):
        raise RegistryValidationError(f"Task {task_id} 'requirement' must be a string")
    req_match = REQUIREMENT_ID_RE.fullmatch(requirement)
    if req_match is None or req_match.group("milestone") != id_match.group("milestone"):
        raise RegistryValidationError(
            f"Invalid or mismatched requirement ID for {task_id}: {requirement!r}"
        )

    title = raw_task["title"]
    if (
        not isinstance(title, str)
        or not title.strip()
        or title != title.strip()
        or "\n" in title
        or "\r" in title
    ):
        raise RegistryValidationError(
            f"Task {task_id} 'title' must be a non-empty single-line trimmed string"
        )

    acceptance = raw_task["acceptance"]
    if (
        not isinstance(acceptance, str)
        or not acceptance.strip()
        or acceptance != acceptance.strip()
        or "\n" in acceptance
        or "\r" in acceptance
    ):
        raise RegistryValidationError(
            f"Task {task_id} 'acceptance' must be a non-empty single-line trimmed string"
        )

    priority = raw_task["priority"]
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise RegistryValidationError(f"Invalid priority for {task_id}: {priority!r}")

    status = raw_task["status"]
    if not isinstance(status, str) or status not in STATUSES:
        raise RegistryValidationError(f"Invalid status: {task_id} ({status!r})")

    raw_deps = raw_task["dependencies"]
    if not isinstance(raw_deps, list):
        raise RegistryValidationError(
            f"Task {task_id} 'dependencies' must be a list of task IDs"
        )
    deps: list[str] = []
    seen_deps: set[str] = set()
    for dep in raw_deps:
        if not isinstance(dep, str) or not dep.strip() or dep != dep.strip():
            raise RegistryValidationError(
                f"Task {task_id} contains an invalid dependency entry: {dep!r}"
            )
        if dep in seen_deps:
            raise RegistryValidationError(
                f"Duplicate dependency {dep!r} in task {task_id}"
            )
        seen_deps.add(dep)
        deps.append(dep)

    return {
        "id": task_id,
        "milestone": milestone,
        "title": title,
        "requirement": requirement,
        "acceptance": acceptance,
        "dependencies": deps,
        "status": status,
        "priority": priority,
    }


def validate(tasks: object, root: Path | None = None) -> dict[str, TaskEntry]:
    effective_root = _validate_root_dir(ROOT if root is None else root)
    if not isinstance(tasks, list) or not tasks:
        raise RegistryValidationError("Tasks collection must be a non-empty list")

    index: dict[str, TaskEntry] = {}
    seen_requirements: set[str] = set()

    for idx, raw_task in enumerate(tasks):
        entry = _validate_task_schema(raw_task, idx)
        task_id = entry["id"]
        requirement = entry["requirement"]
        if task_id in index:
            raise RegistryValidationError(f"Duplicate task ID: {task_id}")
        if requirement in seen_requirements:
            raise RegistryValidationError(f"Duplicate requirement ID: {requirement}")
        seen_requirements.add(requirement)
        index[task_id] = entry

    seen: set[str] = set()
    active: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in active:
            raise RegistryValidationError(f"Dependency cycle: {task_id}")
        if task_id in seen:
            return
        active.add(task_id)
        for dep_id in index[task_id]["dependencies"]:
            if dep_id not in index:
                raise RegistryValidationError(
                    f"Unknown dependency {dep_id} (referenced by {task_id})"
                )
            visit(dep_id)
        active.remove(task_id)
        seen.add(task_id)

    for task_id, entry in index.items():
        visit(task_id)

        task_doc = _require_regular_file(
            effective_root / "docs" / "tasks" / f"{task_id}.md",
            effective_root,
            f"docs/tasks/{task_id}.md",
            max_bytes=MAX_DOC_BYTES,
            missing_message=f"Missing task file: {task_id}",
        )
        if not _read_utf8(task_doc, f"docs/tasks/{task_id}.md").strip():
            raise RegistryValidationError(
                f"Task specification file must not be empty: {task_id}"
            )

        status = entry["status"]
        if status in DEPENDENCY_GATED_STATUSES:
            for dep_id in entry["dependencies"]:
                dep_status = index[dep_id]["status"]
                if dep_status != "VERIFIED":
                    raise RegistryValidationError(
                        f"Unverified dependency: {task_id} ({status}) requires "
                        f"{dep_id} to be VERIFIED (got {dep_status})"
                    )

        if status == "VERIFIED":
            validate_verification_report(
                effective_root / "docs" / "verification" / f"{task_id}.md",
                effective_root,
                task_id,
            )

    return index


def render(
    tasks: Sequence[TaskEntry], index: Mapping[str, TaskEntry]
) -> tuple[str, str]:
    counts = Counter(t["status"] for t in tasks)
    ready = [
        t["id"]
        for t in tasks
        if t["status"] == "NOT_STARTED"
        and all(index[d]["status"] == "VERIFIED" for d in t["dependencies"])
    ]
    lines = [
        "# Progress (generated)",
        "",
        f"Verified: {counts['VERIFIED']}/{len(tasks)}",
        "",
        "Eligible tasks: " + (", ".join(ready) or "None"),
        "",
        "| Milestone | Total | Verified |",
        "|---|---:|---:|",
    ]
    for milestone in sorted({t["milestone"] for t in tasks}):
        rows = [t for t in tasks if t["milestone"] == milestone]
        verified_in_milestone = sum(t["status"] == "VERIFIED" for t in rows)
        lines.append(f"| {milestone} | {len(rows)} | {verified_in_milestone} |")

    trace = [
        "# Requirements traceability (generated)",
        "",
        "| Requirement | Task | Priority | Status |",
        "|---|---|---|---|",
    ]
    trace.extend(
        f"| {t['requirement']} | {t['id']} | {t['priority']} | {t['status']} |"
        for t in tasks
    )
    return "\n".join(lines) + "\n", "\n".join(trace) + "\n"


def validate_repository_progress(
    root: Path = ROOT, *, check_generated: bool = False
) -> ProgressSummary:
    checked_root = _validate_root_dir(root)
    raw_tasks = load_tasks_file(checked_root)
    index = validate(raw_tasks, root=checked_root)
    ordered_tasks = [index[entry["id"]] for entry in raw_tasks]
    progress_md, trace_md = render(ordered_tasks, index)

    if check_generated:
        progress_path = _require_regular_file(
            checked_root / "docs" / "PROGRESS.md",
            checked_root,
            "docs/PROGRESS.md",
            max_bytes=MAX_DOC_BYTES,
        )
        trace_path = _require_regular_file(
            checked_root / "docs" / "REQUIREMENTS_TRACEABILITY.md",
            checked_root,
            "docs/REQUIREMENTS_TRACEABILITY.md",
            max_bytes=MAX_DOC_BYTES,
        )
        if _read_utf8(progress_path, "docs/PROGRESS.md") != progress_md:
            raise RegistryValidationError(
                "docs/PROGRESS.md is out of date with docs/tasks.json; "
                "run python scripts/progress.py --write"
            )
        if _read_utf8(trace_path, "docs/REQUIREMENTS_TRACEABILITY.md") != trace_md:
            raise RegistryValidationError(
                "docs/REQUIREMENTS_TRACEABILITY.md is out of date with docs/tasks.json; "
                "run python scripts/progress.py --write"
            )

    eligible = tuple(
        t["id"]
        for t in ordered_tasks
        if t["status"] == "NOT_STARTED"
        and all(index[d]["status"] == "VERIFIED" for d in t["dependencies"])
    )
    verified_count = sum(t["status"] == "VERIFIED" for t in ordered_tasks)
    return ProgressSummary(
        total=len(ordered_tasks),
        verified=verified_count,
        eligible=eligible,
        progress_markdown=progress_md,
        traceability_markdown=trace_md,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(
        description="Validate VibeOS task registry, verification evidence, and generated tracking reports."
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="Repository root directory containing docs/tasks.json.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Verify task registry, verification reports, and generated tracking docs without writing files.",
    )
    mode_group.add_argument(
        "--write",
        action="store_true",
        help="Regenerate docs/PROGRESS.md and docs/REQUIREMENTS_TRACEABILITY.md after validation succeeds.",
    )

    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except _CLIUsageError as exc:
        print(f"Task registry validation failed: {exc}", file=sys.stderr)
        return 1

    if not isinstance(args.root, str) or not args.root.strip():
        print(
            "Task registry validation failed: --root must be a non-empty path",
            file=sys.stderr,
        )
        return 1

    root_path = Path(args.root)
    try:
        summary = validate_repository_progress(
            root_path, check_generated=bool(args.check)
        )
        if args.write:
            progress_target = _require_safe_output_path(
                root_path / "docs" / "PROGRESS.md",
                root_path,
                "docs/PROGRESS.md",
            )
            trace_target = _require_safe_output_path(
                root_path / "docs" / "REQUIREMENTS_TRACEABILITY.md",
                root_path,
                "docs/REQUIREMENTS_TRACEABILITY.md",
            )
            progress_target.write_text(summary.progress_markdown, encoding="utf-8")
            trace_target.write_text(summary.traceability_markdown, encoding="utf-8")
    except (RegistryValidationError, OSError) as exc:
        print(f"Task registry validation failed: {exc}", file=sys.stderr)
        return 1

    print(summary.progress_markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
