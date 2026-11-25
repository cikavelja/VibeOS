from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _clean_uv_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH"):
        env.pop(key, None)
    env["UV_LINK_MODE"] = "copy"
    env["UV_NO_PROGRESS"] = "1"
    return env


def _copy_clean_backend_checkout(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for filename in ("pyproject.toml", "uv.lock", ".python-version", "README.md"):
        shutil.copy2(BACKEND_ROOT / filename, destination / filename)
    shutil.copytree(
        BACKEND_ROOT / "app",
        destination / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _venv_python(project_dir: Path) -> Path:
    if sys.platform == "win32":
        return project_dir / ".venv" / "Scripts" / "python.exe"
    return project_dir / ".venv" / "bin" / "python"


def test_repository_lockfile_contract_passes() -> None:
    spec = importlib.util.spec_from_file_location(
        "verify_locks", REPO_ROOT / "scripts" / "verify_locks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_locks"] = module
    spec.loader.exec_module(module)
    summary = module.validate_repository_locks(REPO_ROOT)
    assert summary.backend_packages >= 10
    assert summary.frontend_packages >= 20


def test_clean_checkout_uv_sync_locked_and_build_succeeds(tmp_path: Path) -> None:
    uv_bin = shutil.which("uv")
    assert uv_bin is not None, (
        "uv executable must be installed for reproducible lock verification"
    )

    clean_backend = tmp_path / "clean-backend"
    _copy_clean_backend_checkout(clean_backend)
    assert not (clean_backend / ".venv").exists()
    assert not (clean_backend / "dist").exists()

    env = _clean_uv_env()
    sync_proc = subprocess.run(
        [uv_bin, "sync", "--locked", "--no-dev"],
        cwd=clean_backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sync_proc.returncode == 0, f"uv sync --locked failed:\n{sync_proc.stderr}"

    python_exe = _venv_python(clean_backend)
    assert python_exe.is_file()

    smoke_proc = subprocess.run(
        [
            str(python_exe),
            "-c",
            "from app.main import create_app; app = create_app(); assert app.title == 'VibeOS API'",
        ],
        cwd=clean_backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert smoke_proc.returncode == 0, (
        f"Installed app import failed:\n{smoke_proc.stderr}"
    )

    build_proc = subprocess.run(
        [uv_bin, "build"],
        cwd=clean_backend,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build_proc.returncode == 0, f"uv build failed:\n{build_proc.stderr}"

    wheels = list((clean_backend / "dist").glob("vibeos_backend-*.whl"))
    sdists = list((clean_backend / "dist").glob("vibeos_backend-*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        members = set(archive.namelist())
    assert "app/main.py" in members
    assert "app/api/health.py" in members


def test_uv_sync_locked_rejects_drifted_manifest_without_mutating_lockfile(
    tmp_path: Path,
) -> None:
    uv_bin = shutil.which("uv")
    assert uv_bin is not None, (
        "uv executable must be installed for reproducible lock verification"
    )

    drifted_backend = tmp_path / "drifted-backend"
    _copy_clean_backend_checkout(drifted_backend)

    lock_path = drifted_backend / "uv.lock"
    before_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()

    pyproject_path = drifted_backend / "pyproject.toml"
    pyproject_path.write_text(
        pyproject_path.read_text(encoding="utf-8").replace(
            '"fastapi==0.128.2"', '"fastapi==0.128.1"'
        ),
        encoding="utf-8",
    )

    sync_proc = subprocess.run(
        [uv_bin, "sync", "--locked", "--no-dev"],
        cwd=drifted_backend,
        env=_clean_uv_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert sync_proc.returncode != 0
    combined_output = f"{sync_proc.stdout}\n{sync_proc.stderr}".lower()
    assert "lockfile" in combined_output or "uv.lock" in combined_output

    after_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    assert after_digest == before_digest
