from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_locks import LockValidationError, ROOT, main, validate_repository_locks


def _copy_lock_inputs(dest: Path) -> None:
    backend_dest = dest / "backend"
    frontend_dest = dest / "frontend"
    backend_dest.mkdir(parents=True, exist_ok=True)
    frontend_dest.mkdir(parents=True, exist_ok=True)
    for rel in ("pyproject.toml", "uv.lock", ".python-version"):
        shutil.copy2(ROOT / "backend" / rel, backend_dest / rel)
    for rel in ("package.json", "package-lock.json"):
        shutil.copy2(ROOT / "frontend" / rel, frontend_dest / rel)


def _snapshot_tree_digests(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file_path.relative_to(root).as_posix()
        snapshot[rel] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return snapshot


class LockfileValidationTests(unittest.TestCase):
    def test_repository_lockfiles_pass_validation(self) -> None:
        summary = validate_repository_locks(ROOT)
        self.assertGreaterEqual(summary.backend_packages, 10)
        self.assertGreaterEqual(summary.frontend_packages, 20)
        self.assertEqual(main(["--root", str(ROOT)]), 0)

    def test_rejects_missing_lockfiles_and_legacy_requirements_without_side_effects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            (tmp_root / "backend" / "uv.lock").unlink()
            before = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root)]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            (tmp_root / "frontend" / "package-lock.json").unlink()
            with self.assertRaisesRegex(LockValidationError, "package-lock.json"):
                validate_repository_locks(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            (tmp_root / "backend" / "requirements.txt").write_text(
                "fastapi==0.128.2\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(LockValidationError, "requirements.txt"):
                validate_repository_locks(tmp_root)

    def test_rejects_unpinned_dependencies_in_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            pyproject_path = tmp_root / "backend" / "pyproject.toml"
            pyproject_path.write_text(
                pyproject_path.read_text(encoding="utf-8").replace(
                    '"ruff==0.15.12"', '"ruff>=0.15.0"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                LockValidationError, "exact 'name==version' pin"
            ):
                validate_repository_locks(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            pkg_json_path = tmp_root / "frontend" / "package.json"
            data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
            data["dependencies"]["react"] = "^19.3.1"
            pkg_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(LockValidationError, "exact semver pin"):
                validate_repository_locks(tmp_root)

    def test_rejects_manifest_to_lockfile_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            pyproject_path = tmp_root / "backend" / "pyproject.toml"
            pyproject_path.write_text(
                pyproject_path.read_text(encoding="utf-8").replace(
                    '"fastapi==0.128.2"', '"fastapi==0.128.1"'
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                LockValidationError, "drifted from backend/uv.lock"
            ):
                validate_repository_locks(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            pkg_json_path = tmp_root / "frontend" / "package.json"
            data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
            data["dependencies"]["react"] = "19.0.0"
            pkg_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(
                LockValidationError, "drifted from frontend/package-lock.json"
            ):
                validate_repository_locks(tmp_root)

    def test_rejects_missing_hashes_and_insecure_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            uv_lock_path = tmp_root / "backend" / "uv.lock"
            content = uv_lock_path.read_text(encoding="utf-8")
            corrupted = re_replace_first_sha256(content)
            uv_lock_path.write_text(corrupted, encoding="utf-8")
            with self.assertRaisesRegex(LockValidationError, "valid sha256 hash"):
                validate_repository_locks(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            lock_path = tmp_root / "frontend" / "package-lock.json"
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            lock_data["packages"]["node_modules/react"]["resolved"] = (
                "http://registry.npmjs.org/react/-/react-19.3.1.tgz"
            )
            lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(LockValidationError, "must use https:// URL"):
                validate_repository_locks(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            lock_path = tmp_root / "frontend" / "package-lock.json"
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            del lock_data["packages"]["node_modules/react"]["integrity"]
            lock_path.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(LockValidationError, "valid integrity digest"):
                validate_repository_locks(tmp_root)

    def test_rejects_malformed_inputs_and_invalid_cli_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_lock_inputs(tmp_root)
            (tmp_root / "backend" / "uv.lock").write_text(
                "[[package\nbroken =", encoding="utf-8"
            )
            with self.assertRaisesRegex(LockValidationError, "Invalid backend/uv.lock"):
                validate_repository_locks(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            self.assertEqual(main(["--root", str(missing)]), 1)
            self.assertFalse(missing.exists())


def re_replace_first_sha256(content: str) -> str:
    marker = "sha256:"
    pos = content.find(marker)
    if pos < 0:
        raise AssertionError("Expected sha256 hash in uv.lock fixture")
    return content[:pos] + "sha256:invalid" + content[pos + len(marker) + 64 :]


if __name__ == "__main__":
    unittest.main()
