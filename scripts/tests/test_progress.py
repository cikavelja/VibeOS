from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from progress import (
    ROOT,
    STATUSES,
    TASKS,
    RegistryValidationError,
    main,
    parse_tasks_json,
    render,
    validate,
    validate_repository_progress,
    validate_verification_report,
)


def _snapshot_tree_digests(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file_path.relative_to(root).as_posix()
        snapshot[rel] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return snapshot


def _make_task(
    task_id: str = "M00-T01",
    *,
    milestone: str = "M00",
    title: str = "Scaffold React and FastAPI",
    requirement: str = "REQ-00-01",
    acceptance: str = "Frontend renders a health screen and API health endpoint returns success.",
    dependencies: list[str] | None = None,
    status: str = "NOT_STARTED",
    priority: str = "MVP",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "milestone": milestone,
        "title": title,
        "requirement": requirement,
        "acceptance": acceptance,
        "dependencies": [] if dependencies is None else list(dependencies),
        "status": status,
        "priority": priority,
    }


def _valid_verification_markdown(
    task_id: str = "M00-T01",
    *,
    verdict: str = "VERIFIED",
    commit: str = "`fab736e8ceec775bb3bcc5fe22ad0af90e376d42`",
    reviewer: str = "Independent Copilot Verifier",
    ac01_status: str = "PASS",
    ac02_status: str = "PASS",
    ac03_status: str = "PASS",
    ac01_evidence: str = "Verified via `backend/tests/test_api.py::test_health`.",
    ac02_evidence: str = r"Rejects invalid flags (`\|\| true`) and fails closed.",
    ac03_evidence: str = "All regression suites pass with strict typing intact.",
    extra_criterion_row: str | None = None,
    commands_body: str = "| `E:\\AI\\VibeOS` | `pytest -q` | `0` | Passed |",
    ci_body: str = "- Verified commit `fab736e` in GitHub Actions run #1 (`success`).",
    findings_body: str = "None.",
    limitations_body: str = "No blockers.",
    approval_body: str = "Approved by `@cikavelja` on `2026-11-25`.",
) -> str:
    rows = [
        f"| AC-01 | {ac01_status} | {ac01_evidence} |",
        f"| AC-02 | {ac02_status} | {ac02_evidence} |",
        f"| AC-03 | {ac03_status} | {ac03_evidence} |",
    ]
    if extra_criterion_row is not None:
        rows.append(extra_criterion_row)
    criteria_block = "\n".join(rows)
    return (
        f"# Verification — {task_id}\n"
        f"Verdict: {verdict} | Commit: {commit} | Reviewer: {reviewer}\n\n"
        "| Criterion | PASS/FAIL/NOT_RUN | Evidence |\n"
        "|---|---|---|\n"
        f"{criteria_block}\n\n"
        "## Commands and exit codes\n"
        f"{commands_body}\n\n"
        "## CI run and commit\n"
        f"{ci_body}\n\n"
        "## Findings and severity\n"
        f"{findings_body}\n\n"
        "## Limitations and blockers\n"
        f"{limitations_body}\n\n"
        "## Human approval\n"
        f"{approval_body}\n"
    )


def _init_fixture_repo(
    root: Path,
    tasks: list[dict[str, Any]],
    *,
    reports: dict[str, str] | None = None,
    write_generated: bool = True,
) -> None:
    docs_dir = root / "docs"
    tasks_dir = docs_dir / "tasks"
    verif_dir = docs_dir / "verification"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    verif_dir.mkdir(parents=True, exist_ok=True)

    (docs_dir / "tasks.json").write_text(
        json.dumps(tasks, indent=2) + "\n", encoding="utf-8"
    )
    for entry in tasks:
        task_id = entry.get("id")
        if isinstance(task_id, str) and task_id:
            (tasks_dir / f"{task_id}.md").write_text(
                f"# {task_id} — {entry.get('title', 'Task')}\n",
                encoding="utf-8",
            )
    if reports:
        for report_id, content in reports.items():
            (verif_dir / f"{report_id}.md").write_text(content, encoding="utf-8")

    if write_generated:
        index = validate(tasks, root=root)
        ordered = [index[t["id"]] for t in tasks]
        progress_md, trace_md = render(ordered, index)
        (docs_dir / "PROGRESS.md").write_text(progress_md, encoding="utf-8")
        (docs_dir / "REQUIREMENTS_TRACEABILITY.md").write_text(
            trace_md, encoding="utf-8"
        )


class RegistryTests(unittest.TestCase):
    tasks: list[dict[str, Any]]

    def setUp(self) -> None:
        self.tasks = json.loads(TASKS.read_text(encoding="utf-8"))

    def test_baseline(self) -> None:
        index = validate(self.tasks)
        self.assertEqual(len(index), len(self.tasks))
        for report_id in ("M00-T01", "M00-T02", "M00-T03", "M00-T04"):
            validate_verification_report(
                ROOT / "docs" / "verification" / f"{report_id}.md",
                ROOT,
                report_id,
            )

    def test_unknown_dependency(self) -> None:
        self.tasks[0]["dependencies"] = ["UNKNOWN"]
        with self.assertRaisesRegex(ValueError, "Unknown dependency UNKNOWN"):
            validate(self.tasks)

    def test_cycle(self) -> None:
        self.tasks[0]["dependencies"] = [self.tasks[0]["id"]]
        with self.assertRaisesRegex(ValueError, "Dependency cycle"):
            validate(self.tasks)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cyclic_tasks = [
                _make_task(
                    "M00-T01",
                    requirement="REQ-00-01",
                    dependencies=["M00-T03"],
                ),
                _make_task(
                    "M00-T02",
                    requirement="REQ-00-02",
                    dependencies=["M00-T01"],
                ),
                _make_task(
                    "M00-T03",
                    requirement="REQ-00-03",
                    dependencies=["M00-T02"],
                ),
            ]
            _init_fixture_repo(tmp_root, cyclic_tasks, write_generated=False)
            with self.assertRaisesRegex(RegistryValidationError, "Dependency cycle"):
                validate(cyclic_tasks, root=tmp_root)

    def test_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            task_id = str(self.tasks[0]["id"])
            task_doc = tmp_root / "docs" / "tasks" / f"{task_id}.md"
            task_doc.parent.mkdir(parents=True, exist_ok=True)
            task_doc.write_text("# Task\n", encoding="utf-8")
            candidate = [{**self.tasks[0], "status": "VERIFIED", "dependencies": []}]
            with patch("progress.ROOT", tmp_root):
                with self.assertRaisesRegex(
                    ValueError, f"Missing verification report: {task_id}"
                ):
                    validate(candidate)

    def test_duplicate(self) -> None:
        dup_id = [*copy.deepcopy(self.tasks), copy.deepcopy(self.tasks[0])]
        with self.assertRaisesRegex(ValueError, "Duplicate task ID"):
            validate(dup_id)

        dup_req = copy.deepcopy(self.tasks[:2])
        dup_req[1]["requirement"] = dup_req[0]["requirement"]
        with self.assertRaisesRegex(ValueError, "Duplicate requirement ID"):
            validate(dup_req)

    def test_closed_task_schema_and_malformed_json_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            base = _make_task("M00-T01")
            _init_fixture_repo(tmp_root, [base])

            for key in tuple(base.keys()):
                missing_entry = dict(base)
                del missing_entry[key]
                with self.assertRaisesRegex(
                    RegistryValidationError, "missing required keys"
                ):
                    validate([missing_entry], root=tmp_root)

            with_unknown = {**base, "extra_field": "unexpected"}
            with self.assertRaisesRegex(RegistryValidationError, "unknown keys"):
                validate([with_unknown], root=tmp_root)

            invalid_mutations: tuple[tuple[dict[str, Any], str], ...] = (
                ({**base, "id": "M00-T00"}, "Invalid task ID"),
                ({**base, "id": "m00-t01"}, "Invalid task ID"),
                ({**base, "id": "../M00-T01"}, "Invalid task ID"),
                ({**base, "milestone": "M01"}, "Invalid or mismatched milestone"),
                ({**base, "milestone": "M0"}, "Invalid or mismatched milestone"),
                (
                    {**base, "requirement": "REQ-01-01"},
                    "Invalid or mismatched requirement ID",
                ),
                (
                    {**base, "requirement": "REQ-00-00"},
                    "Invalid or mismatched requirement ID",
                ),
                ({**base, "priority": "URGENT"}, "Invalid priority"),
                ({**base, "status": "COMPLETE"}, "Invalid status"),
                ({**base, "title": "   "}, "'title' must be a non-empty"),
                ({**base, "title": " padded "}, "'title' must be a non-empty"),
                ({**base, "acceptance": "line1\nline2"}, "'acceptance' must be"),
                (
                    {**base, "dependencies": "M00-T01"},
                    "'dependencies' must be a list",
                ),
                (
                    {**base, "dependencies": ["  "]},
                    "invalid dependency entry",
                ),
                (
                    {**base, "dependencies": ["M00-T01", "M00-T01"]},
                    "Duplicate dependency",
                ),
            )
            for mutated, expected_msg in invalid_mutations:
                with self.assertRaisesRegex(RegistryValidationError, expected_msg):
                    validate([mutated], root=tmp_root)

            with self.assertRaisesRegex(RegistryValidationError, "non-empty list"):
                validate([], root=tmp_root)
            with self.assertRaisesRegex(RegistryValidationError, "non-empty list"):
                validate({}, root=tmp_root)
            with self.assertRaisesRegex(RegistryValidationError, "must be a mapping"):
                validate(["not-an-object"], root=tmp_root)

            (tmp_root / "docs" / "tasks" / "M00-T01.md").unlink()
            with self.assertRaisesRegex(
                RegistryValidationError, "Missing task file: M00-T01"
            ):
                validate([base], root=tmp_root)

            (tmp_root / "docs" / "tasks" / "M00-T01.md").write_text(
                "   \n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                RegistryValidationError, "Task specification file must not be empty"
            ):
                validate([base], root=tmp_root)

        with self.assertRaisesRegex(RegistryValidationError, "Malformed JSON"):
            parse_tasks_json("[{broken json")
        with self.assertRaisesRegex(RegistryValidationError, "non-empty JSON array"):
            parse_tasks_json('{"id": "M00-T01"}')
        with self.assertRaisesRegex(RegistryValidationError, "Duplicate JSON key"):
            parse_tasks_json(
                '[{"id": "M00-T01", "id": "M00-T02", "milestone": "M00", '
                '"title": "T", "requirement": "REQ-00-01", "acceptance": "A", '
                '"dependencies": [], "status": "NOT_STARTED", "priority": "MVP"}]'
            )
        with self.assertRaisesRegex(
            RegistryValidationError, "Invalid JSON numeric constant"
        ):
            parse_tasks_json("[NaN]")

    def test_rejects_unverified_dependencies_for_active_and_completed_statuses(
        self,
    ) -> None:
        non_verified_upstream = tuple(STATUSES - {"VERIFIED"})
        gated_downstream = (
            "IN_PROGRESS",
            "IMPLEMENTED",
            "VERIFICATION_FAILED",
            "VERIFIED",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            t1 = _make_task("M00-T01", requirement="REQ-00-01")
            t2 = _make_task(
                "M00-T02",
                requirement="REQ-00-02",
                dependencies=["M00-T01"],
            )
            _init_fixture_repo(
                tmp_root,
                [t1, t2],
                reports={
                    "M00-T01": _valid_verification_markdown("M00-T01"),
                    "M00-T02": _valid_verification_markdown("M00-T02"),
                },
                write_generated=False,
            )

            for upstream_status in non_verified_upstream:
                for downstream_status in gated_downstream:
                    candidate = [
                        {**t1, "status": upstream_status},
                        {**t2, "status": downstream_status},
                    ]
                    with self.assertRaisesRegex(
                        RegistryValidationError,
                        r"Unverified dependency: M00-T02",
                    ):
                        validate(candidate, root=tmp_root)

                for allowed_waiting_status in ("NOT_STARTED", "BLOCKED"):
                    waiting_candidate = [
                        {**t1, "status": upstream_status},
                        {**t2, "status": allowed_waiting_status},
                    ]
                    if upstream_status in gated_downstream:
                        # t1 has no dependencies, so any valid status on t1 is fine
                        pass
                    validated = validate(waiting_candidate, root=tmp_root)
                    self.assertEqual(
                        validated["M00-T02"]["status"], allowed_waiting_status
                    )

            valid_verified_pair = [
                {**t1, "status": "VERIFIED"},
                {**t2, "status": "IMPLEMENTED"},
            ]
            validated_pair = validate(valid_verified_pair, root=tmp_root)
            self.assertEqual(validated_pair["M00-T02"]["status"], "IMPLEMENTED")

    def test_rejects_placeholder_and_incomplete_verification_reports(self) -> None:
        template_text = (ROOT / "docs" / "verification" / "TEMPLATE.md").read_text(
            encoding="utf-8"
        )

        invalid_reports: tuple[tuple[str, str], ...] = (
            (template_text, "must start with '# Verification — M00-T01'"),
            (
                template_text.replace("TASK-ID", "M00-T01"),
                "must have 'Verdict: VERIFIED'",
            ),
            (
                _valid_verification_markdown("M00-T99"),
                "must start with '# Verification — M00-T01'",
            ),
            (
                _valid_verification_markdown(verdict="PENDING"),
                "must have 'Verdict: VERIFIED'",
            ),
            (
                _valid_verification_markdown(commit="TBD"),
                "valid lowercase hexadecimal commit hash",
            ),
            (
                _valid_verification_markdown(commit="`0000000`"),
                "valid lowercase hexadecimal commit hash",
            ),
            (
                _valid_verification_markdown(commit="`ABCDEF1`"),
                "valid lowercase hexadecimal commit hash",
            ),
            (
                _valid_verification_markdown(reviewer="TBD"),
                "non-placeholder Reviewer",
            ),
            (
                _valid_verification_markdown(ac01_status="NOT_RUN"),
                "criterion AC-01 must be 'PASS'",
            ),
            (
                _valid_verification_markdown(ac02_status="FAIL"),
                "criterion AC-02 must be 'PASS'",
            ),
            (
                _valid_verification_markdown(ac03_status="PENDING"),
                "criterion AC-03 must be 'PASS'",
            ),
            (
                _valid_verification_markdown(
                    extra_criterion_row="| AC-04 | NOT_RUN | Deferred check |"
                ),
                "criterion AC-04 must be 'PASS'",
            ),
            (
                _valid_verification_markdown(ac01_evidence=""),
                "criterion AC-01 is missing substantive evidence",
            ),
            (
                _valid_verification_markdown(ac02_evidence="TBD"),
                "criterion AC-02 is missing substantive evidence",
            ),
            (
                _valid_verification_markdown(commands_body=""),
                "section '## Commands and exit codes' must be non-empty",
            ),
            (
                _valid_verification_markdown(commands_body="<!-- empty -->"),
                "section '## Commands and exit codes' must be non-empty",
            ),
            (
                _valid_verification_markdown(ci_body="TBD"),
                "section '## CI run and commit' must be non-empty",
            ),
            (
                _valid_verification_markdown(findings_body=""),
                "section '## Findings and severity' must be non-empty",
            ),
            (
                _valid_verification_markdown(limitations_body="TODO"),
                "section '## Limitations and blockers' must be non-empty",
            ),
            (
                _valid_verification_markdown(approval_body="None"),
                "section '## Human approval' must be non-empty",
            ),
            (
                _valid_verification_markdown(approval_body="Pending human approval"),
                "section '## Human approval' must record completed approval",
            ),
            (
                _valid_verification_markdown().replace("## Human approval\n", ""),
                "missing required section '## Human approval'",
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            task = _make_task("M00-T01", status="VERIFIED")
            _init_fixture_repo(
                tmp_root,
                [task],
                reports={"M00-T01": _valid_verification_markdown("M00-T01")},
            )
            report_file = tmp_root / "docs" / "verification" / "M00-T01.md"

            for bad_markdown, expected_pattern in invalid_reports:
                report_file.write_text(bad_markdown, encoding="utf-8")
                with self.assertRaisesRegex(RegistryValidationError, expected_pattern):
                    validate([task], root=tmp_root)

            missing_ac03 = "\n".join(
                line
                for line in _valid_verification_markdown("M00-T01").splitlines()
                if not line.startswith("| AC-03 |")
            )
            report_file.write_text(missing_ac03 + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                RegistryValidationError, "missing mandatory criterion AC-03"
            ):
                validate([task], root=tmp_root)

            no_criteria_rows = "\n".join(
                line
                for line in _valid_verification_markdown("M00-T01").splitlines()
                if not line.startswith("| AC-0")
            )
            report_file.write_text(no_criteria_rows + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                RegistryValidationError,
                "missing a complete acceptance criteria table",
            ):
                validate([task], root=tmp_root)

    def test_cli_check_and_write_modes_and_tree_immutability_on_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("M00-T01", requirement="REQ-00-01", status="VERIFIED"),
                _make_task(
                    "M00-T02",
                    requirement="REQ-00-02",
                    dependencies=["M00-T01"],
                    status="NOT_STARTED",
                ),
            ]
            _init_fixture_repo(
                tmp_root,
                tasks,
                reports={"M00-T01": _valid_verification_markdown("M00-T01")},
                write_generated=False,
            )

            before_missing_generated = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root), "--check"]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before_missing_generated)

            self.assertEqual(main(["--root", str(tmp_root), "--write"]), 0)
            self.assertTrue((tmp_root / "docs" / "PROGRESS.md").is_file())
            self.assertTrue(
                (tmp_root / "docs" / "REQUIREMENTS_TRACEABILITY.md").is_file()
            )

            synced_snapshot = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root), "--check"]), 0)
            self.assertEqual(main(["--root", str(tmp_root)]), 0)
            self.assertEqual(_snapshot_tree_digests(tmp_root), synced_snapshot)

            summary = validate_repository_progress(tmp_root, check_generated=True)
            self.assertEqual(summary.total, 2)
            self.assertEqual(summary.verified, 1)
            self.assertEqual(summary.eligible, ("M00-T02",))

            # Drift PROGRESS.md -> --check must fail closed without mutating files
            (tmp_root / "docs" / "PROGRESS.md").write_text(
                "# Stale progress\n", encoding="utf-8"
            )
            drifted_snapshot = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root), "--check"]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), drifted_snapshot)

            # Restore clean generated files, then introduce invalid completion claim
            self.assertEqual(main(["--root", str(tmp_root), "--write"]), 0)
            tasks[1]["status"] = "VERIFIED"
            (tmp_root / "docs" / "tasks.json").write_text(
                json.dumps(tasks, indent=2) + "\n", encoding="utf-8"
            )
            invalid_snapshot = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root), "--write"]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), invalid_snapshot)

            # Mutually exclusive flags, unknown flags, and invalid --root fail closed
            self.assertEqual(main(["--root", str(tmp_root), "--check", "--write"]), 1)
            self.assertEqual(main(["--root", str(tmp_root), "--bogus-flag"]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), invalid_snapshot)

            nonexistent = tmp_root / "does-not-exist"
            self.assertEqual(main(["--root", str(nonexistent), "--write"]), 1)
            self.assertFalse(nonexistent.exists())
            self.assertEqual(main(["--root", "   "]), 1)

            # Real subprocess CLI exit codes
            proc_fail = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "progress.py"),
                    "--root",
                    str(tmp_root),
                    "--write",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc_fail.returncode, 1)
            self.assertIn("Missing verification report: M00-T02", proc_fail.stderr)
            self.assertEqual(_snapshot_tree_digests(tmp_root), invalid_snapshot)

    def test_rejects_symlinks_without_mutating_outputs(self) -> None:
        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as outside_tmp,
        ):
            tmp_root = Path(repo_tmp)
            outside_dir = Path(outside_tmp)
            task = _make_task("M00-T01", status="VERIFIED")
            _init_fixture_repo(
                tmp_root,
                [task],
                reports={"M00-T01": _valid_verification_markdown("M00-T01")},
            )

            outside_file = outside_dir / "outside.md"
            outside_file.write_text("# Outside\n", encoding="utf-8")

            # Real OS symlink test when environment allows creating symlinks
            symlink_root = outside_dir / "linked-root"
            created_symlink = False
            try:
                symlink_root.symlink_to(tmp_root, target_is_directory=True)
                created_symlink = True
            except OSError:
                created_symlink = False

            if created_symlink:
                self.assertEqual(main(["--root", str(symlink_root), "--check"]), 1)

            # Deterministic symlink checks for root, input files, and output targets
            with patch.object(Path, "is_symlink", return_value=True):
                self.assertEqual(main(["--root", str(tmp_root), "--check"]), 1)

            trace_path = tmp_root / "docs" / "REQUIREMENTS_TRACEABILITY.md"
            original_is_symlink = Path.is_symlink

            def fake_trace_symlink(path_obj: Path) -> bool:
                if path_obj.name == "REQUIREMENTS_TRACEABILITY.md":
                    return True
                return original_is_symlink(path_obj)

            # Mutate tasks.json slightly (still valid) and verify --write aborts BEFORE
            # touching PROGRESS.md if REQUIREMENTS_TRACEABILITY.md is a symlink
            updated_task = _make_task("M00-T01", status="NOT_STARTED")
            (tmp_root / "docs" / "tasks.json").write_text(
                json.dumps([updated_task], indent=2) + "\n", encoding="utf-8"
            )
            before_write = _snapshot_tree_digests(tmp_root)
            with patch.object(Path, "is_symlink", fake_trace_symlink):
                self.assertEqual(main(["--root", str(tmp_root), "--write"]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before_write)
            self.assertTrue(trace_path.is_file())


if __name__ == "__main__":
    unittest.main()
