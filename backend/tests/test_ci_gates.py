from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _load_verify_ci() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "verify_ci", REPO_ROOT / "scripts" / "verify_ci.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_ci"] = module
    spec.loader.exec_module(module)
    return module


def test_ci_workflow_contracts_enforce_backend_frontend_and_compose_gates() -> None:
    verify_ci = _load_verify_ci()
    summary = verify_ci.validate_repository_ci(REPO_ROOT)
    assert summary.workflows == ("planning.yml", "product-preview.yml")
    assert summary.jobs == 4


def test_backend_quality_gates_fail_closed_on_deliberately_failing_checks(
    tmp_path: Path,
) -> None:
    verify_ci = _load_verify_ci()
    GateCommandSpec = verify_ci.GateCommandSpec

    clean_pkg = tmp_path / "clean_pkg"
    clean_pkg.mkdir()
    sample_mod = clean_pkg / "sample.py"
    sample_mod.write_text(
        "def add_numbers(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    sample_test = clean_pkg / "test_sample.py"
    sample_test.write_text(
        "from sample import add_numbers\n\n\n"
        "def test_add_numbers() -> None:\n"
        "    assert add_numbers(2, 3) == 5\n",
        encoding="utf-8",
    )

    clean_report = verify_ci.run_gate_commands(
        [
            GateCommandSpec(
                name="ruff-check",
                argv=(sys.executable, "-m", "ruff", "check", "."),
                cwd=clean_pkg,
            ),
            GateCommandSpec(
                name="ruff-format",
                argv=(sys.executable, "-m", "ruff", "format", "--check", "."),
                cwd=clean_pkg,
            ),
            GateCommandSpec(
                name="mypy-strict",
                argv=(sys.executable, "-m", "mypy", "--strict", "."),
                cwd=clean_pkg,
            ),
            GateCommandSpec(
                name="pytest",
                argv=(sys.executable, "-m", "pytest", "-q"),
                cwd=clean_pkg,
                env={"PYTHONPATH": str(clean_pkg)},
            ),
        ]
    )
    assert clean_report.passed is True
    assert clean_report.exit_code == 0
    assert len(clean_report.results) == 4

    failing_test_dir = tmp_path / "failing_pytest"
    failing_test_dir.mkdir()
    (failing_test_dir / "test_deliberate_fail.py").write_text(
        "def test_deliberate_backend_regression() -> None:\n"
        "    assert {'status': 'ok'} == {'status': 'broken'}\n",
        encoding="utf-8",
    )
    sentinel = failing_test_dir / "post_failure_marker.txt"

    pytest_fail_report = verify_ci.run_gate_commands(
        [
            GateCommandSpec(
                name="pytest-deliberate-fail",
                argv=(sys.executable, "-m", "pytest", "-q"),
                cwd=failing_test_dir,
            ),
            GateCommandSpec(
                name="post-failure-step",
                argv=(
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('post_failure_marker.txt').write_text('bad')",
                ),
                cwd=failing_test_dir,
            ),
        ]
    )
    assert pytest_fail_report.passed is False
    assert pytest_fail_report.exit_code != 0
    assert len(pytest_fail_report.results) == 1
    assert not sentinel.exists()
    assert "test_deliberate_backend_regression" in (
        pytest_fail_report.results[0].stdout + pytest_fail_report.results[0].stderr
    )

    bad_type_dir = tmp_path / "failing_mypy"
    bad_type_dir.mkdir()
    (bad_type_dir / "untyped.py").write_text(
        "def untyped_helper(value):\n    return value\n",
        encoding="utf-8",
    )
    mypy_fail_report = verify_ci.run_gate_commands(
        [
            GateCommandSpec(
                name="mypy-strict-fail",
                argv=(sys.executable, "-m", "mypy", "--strict", "untyped.py"),
                cwd=bad_type_dir,
            )
        ]
    )
    assert mypy_fail_report.passed is False
    assert mypy_fail_report.exit_code != 0

    bad_lint_dir = tmp_path / "failing_ruff"
    bad_lint_dir.mkdir()
    (bad_lint_dir / "unused_import.py").write_text(
        "import os\n\nx: int = 1\n",
        encoding="utf-8",
    )
    ruff_fail_report = verify_ci.run_gate_commands(
        [
            GateCommandSpec(
                name="ruff-check-fail",
                argv=(sys.executable, "-m", "ruff", "check", "unused_import.py"),
                cwd=bad_lint_dir,
            )
        ]
    )
    assert ruff_fail_report.passed is False
    assert ruff_fail_report.exit_code != 0
