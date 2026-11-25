from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_compose import (
    ComposeValidationError,
    ROOT,
    main,
    validate_repository_compose,
)


def _copy_compose_inputs(dest: Path) -> None:
    backend_dest = dest / "backend"
    frontend_dest = dest / "frontend"
    backend_dest.mkdir(parents=True, exist_ok=True)
    frontend_dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "docker-compose.yml", dest / "docker-compose.yml")
    for rel in ("Dockerfile", ".dockerignore"):
        shutil.copy2(ROOT / "backend" / rel, backend_dest / rel)
        shutil.copy2(ROOT / "frontend" / rel, frontend_dest / rel)


def _snapshot_tree_digests(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file_path.relative_to(root).as_posix()
        snapshot[rel] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return snapshot


class ComposeValidationTests(unittest.TestCase):
    def test_repository_compose_contract_passes(self) -> None:
        summary = validate_repository_compose(ROOT)
        self.assertEqual(summary.services, ("postgres", "backend", "frontend"))
        self.assertEqual(summary.dockerfiles, 2)
        self.assertEqual(main(["--root", str(ROOT)]), 0)

    def test_rejects_missing_files_without_mutating_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            (tmp_root / "docker-compose.yml").unlink()
            before = _snapshot_tree_digests(tmp_root)
            self.assertEqual(main(["--root", str(tmp_root)]), 1)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            (tmp_root / "frontend" / "Dockerfile").unlink()
            with self.assertRaisesRegex(ComposeValidationError, "frontend/Dockerfile"):
                validate_repository_compose(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nonexistent-root"
            self.assertEqual(main(["--root", str(missing)]), 1)
            self.assertFalse(missing.exists())
            self.assertEqual(main(["--root", "   "]), 1)

    def test_rejects_missing_services_and_weak_depends_on_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            compose_path = tmp_root / "docker-compose.yml"
            compose_path.write_text(
                "services:\n"
                "  backend:\n"
                "    build: ./backend\n"
                "  frontend:\n"
                "    build: ./frontend\n"
                "volumes:\n"
                "  postgres-data:\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ComposeValidationError, "missing required services: \\['postgres'\\]"
            ):
                validate_repository_compose(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            compose_path = tmp_root / "docker-compose.yml"
            compose_path.write_text(
                compose_path.read_text(encoding="utf-8").replace(
                    "condition: service_healthy", "condition: service_started", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ComposeValidationError, "condition: service_healthy"
            ):
                validate_repository_compose(tmp_root)

    def test_rejects_public_port_bindings_and_disabled_healthchecks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            compose_path = tmp_root / "docker-compose.yml"
            compose_path.write_text(
                compose_path.read_text(encoding="utf-8").replace(
                    "127.0.0.1:${BACKEND_PORT:-8000}:8000", "0.0.0.0:8000:8000"
                ),
                encoding="utf-8",
            )
            before = _snapshot_tree_digests(tmp_root)
            with self.assertRaisesRegex(
                ComposeValidationError, "bind exclusively to 127.0.0.1"
            ):
                validate_repository_compose(tmp_root)
            self.assertEqual(_snapshot_tree_digests(tmp_root), before)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            compose_path = tmp_root / "docker-compose.yml"
            compose_path.write_text(
                compose_path.read_text(encoding="utf-8").replace(
                    "127.0.0.1:${FRONTEND_PORT:-5173}:5173", "5173:5173"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ComposeValidationError, "bind exclusively to 127.0.0.1"
            ):
                validate_repository_compose(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            compose_path = tmp_root / "docker-compose.yml"
            content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
            mutated = content.replace(
                "    healthcheck:\n",
                "    healthcheck:\n      disable: true\n",
                1,
            )
            compose_path.write_text(mutated, encoding="utf-8")
            with self.assertRaisesRegex(
                ComposeValidationError, "healthcheck must not be disabled"
            ):
                validate_repository_compose(tmp_root)

    def test_rejects_unpinned_images_and_root_users_in_dockerfiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            compose_path = tmp_root / "docker-compose.yml"
            compose_path.write_text(
                compose_path.read_text(encoding="utf-8").replace(
                    "postgres:16.16-alpine", "postgres:latest"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ComposeValidationError, "numeric version tag"):
                validate_repository_compose(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            fe_dockerfile = tmp_root / "frontend" / "Dockerfile"
            fe_dockerfile.write_text(
                fe_dockerfile.read_text(encoding="utf-8").replace("USER node\n", ""),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ComposeValidationError, "non-root USER instruction"
            ):
                validate_repository_compose(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            be_dockerfile = tmp_root / "backend" / "Dockerfile"
            be_dockerfile.write_text(
                be_dockerfile.read_text(encoding="utf-8").replace(
                    "USER 10001:10001", "USER 0:0"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ComposeValidationError, "must not run as root USER"
            ):
                validate_repository_compose(tmp_root)

    def test_rejects_malformed_yaml_tabs_duplicate_keys_and_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            (tmp_root / "docker-compose.yml").write_text(
                "services:\n\tpostgres:\n\t\timage: postgres:16.16-alpine\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ComposeValidationError, "Tab indentation"):
                validate_repository_compose(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            (tmp_root / "docker-compose.yml").write_text(
                "services:\n  postgres: &pg\n    image: postgres:16.16-alpine\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ComposeValidationError, "anchors"):
                validate_repository_compose(tmp_root)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _copy_compose_inputs(tmp_root)
            (tmp_root / "docker-compose.yml").write_text(
                "services:\n  postgres:\n    image: postgres:16.16-alpine\n"
                "  postgres:\n    image: postgres:16.16-alpine\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ComposeValidationError, "Duplicate YAML key"):
                validate_repository_compose(tmp_root)


if __name__ == "__main__":
    unittest.main()
