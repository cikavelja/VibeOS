from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_ci import (
    CIValidationError,
    GateCommandSpec,
    ROOT,
    main,
    parse_workflow_yaml,
    run_gate_commands,
    validate_repository_ci,
    validate_workflow_file,
)


def _copy_ci_inputs(dest: Path) -> None:
    workflows_dest = dest / ".github" / "workflows"
    workflows_dest.mkdir(parents=True, exist_ok=True)
    for wf_name in ("planning.yml", "product-preview.yml"):
        normalized = (
            (ROOT / ".github" / "workflows" / wf_name)
            .read_text(encoding="utf-8")
            .replace("\r\n", "\n")
        )
        (workflows_dest / wf_name).write_text(
            normalized, encoding="utf-8", newline="\n"
        )


def _snapshot_tree_digests(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file_path.relative_to(root).as_posix()
        snapshot[rel] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return snapshot


def _resolve_pytest_argv() -> tuple[str, ...]:
    win_py = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    if win_py.is_file():
        return (str(win_py), "-m", "pytest")
    posix_py = ROOT / "backend" / ".venv" / "bin" / "python"
    if posix_py.is_file():
        return (str(posix_py), "-m", "pytest")
    uv_bin = shutil.which("uv")
    if uv_bin is not None:
        return (uv_bin, "run", "--project", str(ROOT / "backend"), "pytest")
    return (sys.executable, "-m", "pytest")


class CIValidationTests(unittest.TestCase):
    def test_repository_ci_workflows_pass_contract_validation(self) -> None:
        summary = validate_repository_ci(ROOT)
        self.assertEqual(summary.workflows, ("planning.yml", "product-preview.yml"))
        self.assertEqual(summary.jobs, 4)
        self.assertEqual(main(["--root", str(ROOT)]), 0)

        preview = parse_workflow_yaml(
            (ROOT / ".github" / "workflows" / "product-preview.yml").read_text(
                encoding="utf-8"
            ),
            "product-preview.yml",
        )
        self.assertEqual(preview["permissions"], {"contents": "read"})
        self.assertEqual(preview["on"], ["push", "pull_request"])
        self.assertEqual(
            set(preview["jobs"].keys()), {"backend", "frontend", "docker-compose"}
        )

        planning = parse_workflow_yaml(
            (ROOT / ".github" / "workflows" / "planning.yml").read_text(
                encoding="utf-8"
            ),
            "planning.yml",
        )
        self.assertEqual(planning["permissions"], {"contents": "read"})
        self.assertEqual(planning["on"], ["push", "pull_request"])
        self.assertEqual(set(planning["jobs"].keys()), {"planning"})

    def test_rejects_missing_workflows_and_invalid_roots_without_mutating_tree(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            (tmp_root / ".github" / "workflows" / "product-preview.yml").unlink()
            before = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root)]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            (tmp_root / ".github" / "workflows" / "planning.yml").unlink()
            before = _snapshot_tree_digests(tmp_root)
            with self.assertRaisesRegex(CIValidationError, "planning.yml"):
                validate_repository_ci(tmp_root)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            (tmp_root / ".github" / "workflows" / " stray.txt").write_text(
                "not a workflow\n", encoding="utf-8"
            )
            before = _snapshot_tree_digests(tmp_root)
            with self.assertRaisesRegex(CIValidationError, "Unexpected non-YAML file"):
                validate_repository_ci(tmp_root)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nonexistent-ci-root"
            self.assertEqual(main(["--root", str(missing)]), 1)
            self.assertFalse(missing.exists())
            self.assertEqual(main(["--root", "   "]), 1)

    def test_rejects_symlinks_and_root_escape_paths(self) -> None:
        with (
            tempfile.TemporaryDirectory() as repo_tmp,
            tempfile.TemporaryDirectory() as outside_tmp,
        ):
            tmp_root = Path(repo_tmp)
            _copy_ci_inputs(tmp_root)
            outside_file = Path(outside_tmp) / "escaped.yml"
            shutil.copy2(
                ROOT / ".github" / "workflows" / "planning.yml",
                outside_file,
            )

            with self.assertRaisesRegex(CIValidationError, "escapes repository root"):
                validate_workflow_file(outside_file, tmp_root)

            symlink_target = tmp_root / ".github" / "workflows" / "escaped.yml"
            created_symlink = False
            try:
                symlink_target.symlink_to(outside_file)
                created_symlink = True
            except OSError:
                created_symlink = False

            if created_symlink:
                with self.assertRaisesRegex(CIValidationError, "must not be a symlink"):
                    validate_repository_ci(tmp_root)
                symlink_target.unlink()

            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaisesRegex(CIValidationError, "must not be a symlink"):
                    validate_repository_ci(tmp_root)

    def test_rejects_missing_or_escalated_permissions_and_unsafe_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "planning.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "permissions:\n  contents: read\n", ""
                ),
                encoding="utf-8",
            )
            before = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root)]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "product-preview.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "contents: read", "contents: write"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CIValidationError, "exceeds least-privilege"):
                validate_repository_ci(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "planning.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "on: [push, pull_request]", "on: [push, pull_request_target]"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CIValidationError, "dangerous triggers"):
                validate_repository_ci(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "planning.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "on: [push, pull_request]", "on: [push]"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CIValidationError, "pull_request"):
                validate_repository_ci(tmp_root)

    def test_rejects_continue_on_error_exit_masking_and_invalid_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "product-preview.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "      - run: uv run pytest -q\n",
                    "      - run: uv run pytest -q\n        continue-on-error: true\n",
                ),
                encoding="utf-8",
            )
            before = _snapshot_tree_digests(tmp_root)
            with self.assertRaisesRegex(CIValidationError, "continue-on-error"):
                validate_repository_ci(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root)]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        for masked_cmd in (
            "uv run pytest -q || true",
            "uv run pytest -q || exit 0",
            "set +e; uv run pytest -q",
            "uv run pytest --collect-only",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                _copy_ci_inputs(tmp_root)
                wf_path = tmp_root / ".github" / "workflows" / "product-preview.yml"
                wf_path.write_text(
                    wf_path.read_text(encoding="utf-8").replace(
                        "uv run pytest -q", masked_cmd
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(CIValidationError):
                    validate_repository_ci(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "product-preview.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "timeout-minutes: 15", "timeout-minutes: 120", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CIValidationError, "timeout-minutes"):
                validate_repository_ci(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "planning.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "actions/checkout@v4", "actions/checkout@main"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CIValidationError, "pin an explicit version"):
                validate_repository_ci(tmp_root)

    def test_rejects_missing_mandatory_quality_gate_steps(self) -> None:
        product_removals = (
            ("      - run: uv run mypy --strict app tests\n", "mypy --strict"),
            ("      - run: npm run test\n", "npm run test"),
            ("      - run: npm run e2e\n", "npm run e2e"),
            ("        if: always()\n", "if: always\\(\\)"),
        )
        for snippet, expected_msg in product_removals:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                _copy_ci_inputs(tmp_root)
                wf_path = tmp_root / ".github" / "workflows" / "product-preview.yml"
                wf_path.write_text(
                    wf_path.read_text(encoding="utf-8").replace(snippet, ""),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(CIValidationError, expected_msg):
                    validate_repository_ci(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            wf_path = tmp_root / ".github" / "workflows" / "planning.yml"
            wf_path.write_text(
                wf_path.read_text(encoding="utf-8").replace(
                    "      - run: python scripts/verify_ci.py\n", ""
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CIValidationError, "scripts/verify_ci.py"):
                validate_repository_ci(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_ci_inputs(tmp_root)
            (tmp_root / ".github" / "workflows" / "extra.yml").write_text(
                "name: Extra\non: [push, pull_request]\njobs:\n  noop:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n      - run: echo ok\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CIValidationError, "permissions"):
                validate_repository_ci(tmp_root)

    def test_rejects_malformed_yaml_tabs_anchors_and_duplicate_keys(self) -> None:
        malformed_cases = (
            ("name: Bad\n\ton: [push, pull_request]\n", "Tab indentation"),
            ("name: &ref Bad\non: [push, pull_request]\n", "anchors"),
            (
                "name: Bad\nname: Duplicate\non: [push, pull_request]\n",
                "Duplicate YAML key",
            ),
            ('name: "unterminated\non: [push, pull_request]\n', "Unclosed quote"),
            ("name: |\non: [push, pull_request]\n", "Empty YAML block scalar"),
        )
        for raw_yaml, expected_pattern in malformed_cases:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                _copy_ci_inputs(tmp_root)
                (tmp_root / ".github" / "workflows" / "planning.yml").write_text(
                    raw_yaml, encoding="utf-8"
                )
                before = _snapshot_tree_digests(tmp_root)
                with self.assertRaisesRegex(CIValidationError, expected_pattern):
                    validate_repository_ci(tmp_root)
                self.assertEqual(_snapshot_tree_digests(tmp_root), before)


class GateRunnerTests(unittest.TestCase):
    def test_gate_runner_executes_real_pytest_and_fails_on_deliberate_failing_test(
        self,
    ) -> None:
        pytest_argv = _resolve_pytest_argv()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            passing_test = tmp_dir / "test_passing_gate.py"
            passing_test.write_text(
                "def test_ok() -> None:\n    assert 2 + 2 == 4\n",
                encoding="utf-8",
            )
            failing_test = tmp_dir / "test_deliberate_failure.py"
            failing_test.write_text(
                "def test_deliberate_ci_failure() -> None:\n"
                "    assert 1 == 2, 'deliberate backend failure for AC-01'\n",
                encoding="utf-8",
            )
            sentinel_file = tmp_dir / "sentinel.txt"

            pass_report = run_gate_commands(
                [
                    GateCommandSpec(
                        name="pytest-pass",
                        argv=(*pytest_argv, "-q", str(passing_test)),
                        cwd=tmp_dir,
                    ),
                    GateCommandSpec(
                        name="python-smoke",
                        argv=(sys.executable, "-c", "assert True"),
                        cwd=tmp_dir,
                    ),
                ]
            )
            self.assertTrue(pass_report.passed)
            self.assertEqual(pass_report.exit_code, 0)
            self.assertEqual(len(pass_report.results), 2)

            fail_fast_report = run_gate_commands(
                [
                    GateCommandSpec(
                        name="pytest-deliberate-fail",
                        argv=(*pytest_argv, "-q", str(failing_test)),
                        cwd=tmp_dir,
                    ),
                    GateCommandSpec(
                        name="unreachable-sentinel",
                        argv=(
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('sentinel.txt').write_text('ran')",
                        ),
                        cwd=tmp_dir,
                    ),
                ],
                fail_fast=True,
            )
            self.assertFalse(fail_fast_report.passed)
            self.assertNotEqual(fail_fast_report.exit_code, 0)
            self.assertEqual(len(fail_fast_report.results), 1)
            self.assertFalse(sentinel_file.exists())
            combined_out = (
                fail_fast_report.results[0].stdout
                + "\n"
                + fail_fast_report.results[0].stderr
            )
            self.assertIn("test_deliberate_ci_failure", combined_out)

            full_report = run_gate_commands(
                [
                    GateCommandSpec(
                        name="pytest-deliberate-fail",
                        argv=(*pytest_argv, "-q", str(failing_test)),
                        cwd=tmp_dir,
                    ),
                    GateCommandSpec(
                        name="passing-followup",
                        argv=(sys.executable, "-c", "print('still ran')"),
                        cwd=tmp_dir,
                    ),
                ],
                fail_fast=False,
            )
            self.assertFalse(full_report.passed)
            self.assertNotEqual(full_report.exit_code, 0)
            self.assertEqual(len(full_report.results), 2)
            self.assertNotEqual(full_report.results[0].returncode, 0)
            self.assertEqual(full_report.results[1].returncode, 0)

    def test_gate_runner_handles_timeouts_missing_binaries_and_invalid_specs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)

            timeout_report = run_gate_commands(
                [
                    GateCommandSpec(
                        name="hang-check",
                        argv=(sys.executable, "-c", "import time; time.sleep(5)"),
                        cwd=tmp_dir,
                        timeout_seconds=0.2,
                    )
                ]
            )
            self.assertFalse(timeout_report.passed)
            self.assertEqual(timeout_report.exit_code, 124)
            self.assertIn("timed out", timeout_report.results[0].stderr)

            missing_bin_report = run_gate_commands(
                [
                    GateCommandSpec(
                        name="missing-binary",
                        argv=("nonexistent-vibeos-gate-binary-9999", "--version"),
                        cwd=tmp_dir,
                    )
                ]
            )
            self.assertFalse(missing_bin_report.passed)
            self.assertEqual(missing_bin_report.exit_code, 127)

            with self.assertRaisesRegex(CIValidationError, "must not be empty"):
                run_gate_commands([])

            with self.assertRaisesRegex(CIValidationError, "exit-code masking"):
                run_gate_commands(
                    [
                        GateCommandSpec(
                            name="masked",
                            argv=("sh", "-c", "pytest || true"),
                            cwd=tmp_dir,
                        )
                    ]
                )

            with self.assertRaisesRegex(CIValidationError, "timeout_seconds"):
                run_gate_commands(
                    [
                        GateCommandSpec(
                            name="zero-timeout",
                            argv=(sys.executable, "-V"),
                            cwd=tmp_dir,
                            timeout_seconds=0,
                        )
                    ]
                )


if __name__ == "__main__":
    unittest.main()
