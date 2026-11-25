from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

PINNED_PEP508_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[A-Za-z0-9,._-]+\])?==(?P<version>[A-Za-z0-9.+!_-]+)$"
)
EXACT_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
SHA256_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SRI_INTEGRITY_RE = re.compile(r"^sha(?:256|384|512)-[A-Za-z0-9+/=]{32,}$")
REQUIRED_FRONTEND_SCRIPTS = ("build", "typecheck", "lint", "test", "e2e")
REQUIRED_VITE_PLATFORM_PACKAGES = (
    "node_modules/@esbuild/linux-x64",
    "node_modules/@esbuild/win32-x64",
    "node_modules/@rollup/rollup-linux-x64-gnu",
    "node_modules/@rollup/rollup-linux-x64-musl",
    "node_modules/@rollup/rollup-win32-x64-msvc",
)


class LockValidationError(ValueError):
    """Raised when dependency manifests or lockfiles violate reproducibility rules."""


@dataclass(frozen=True)
class LockSummary:
    backend_packages: int
    frontend_packages: int


def _normalize_pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _require_regular_file(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise LockValidationError(f"{label} must not be a symlink: {path}")
    if not path.exists() or not path.is_file():
        raise LockValidationError(f"Missing required {label}: {path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise LockValidationError(f"{label} escapes repository root: {path}") from exc
    return path


def _require_https_url(url: object, context: str) -> None:
    if not isinstance(url, str) or not url.strip():
        raise LockValidationError(f"{context} must declare a non-empty URL")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise LockValidationError(f"{context} must use https:// URL, got: {url!r}")


def _parse_pinned_pep508_list(raw: object, section: str) -> dict[str, str]:
    if not isinstance(raw, list) or not raw:
        raise LockValidationError(
            f"{section} must be a non-empty list of pinned dependencies"
        )
    parsed: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, str):
            raise LockValidationError(f"{section} entries must be strings")
        stripped = entry.strip()
        match = PINNED_PEP508_RE.match(stripped)
        if match is None:
            raise LockValidationError(
                f"{section} entry must use exact 'name==version' pin, got: {entry!r}"
            )
        norm_name = _normalize_pep503(match.group("name"))
        if norm_name in parsed:
            raise LockValidationError(
                f"Duplicate dependency {norm_name!r} in {section}"
            )
        parsed[norm_name] = match.group("version")
    return parsed


def _validate_uv_artifact(artifact: object, context: str) -> None:
    if not isinstance(artifact, dict):
        raise LockValidationError(f"{context} must be a table")
    digest = artifact.get("hash")
    if not isinstance(digest, str) or SHA256_HASH_RE.match(digest) is None:
        raise LockValidationError(f"{context} is missing a valid sha256 hash")
    if "url" in artifact:
        _require_https_url(artifact["url"], f"{context}.url")


def _extract_uv_metadata_specs(entries: object, context: str) -> dict[str, str]:
    if not isinstance(entries, list):
        raise LockValidationError(f"{context} must be a list in uv.lock")
    specs: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise LockValidationError(f"{context} entry must be a table in uv.lock")
        name = item.get("name")
        specifier = item.get("specifier")
        if not isinstance(name, str) or not isinstance(specifier, str):
            raise LockValidationError(
                f"{context} entry in uv.lock must include exact name and specifier"
            )
        if not specifier.startswith("=="):
            raise LockValidationError(
                f"{context} entry for {name!r} in uv.lock must use '==', got {specifier!r}"
            )
        specs[_normalize_pep503(name)] = specifier[2:]
    return specs


def validate_backend_locks(root: Path) -> int:
    backend_dir = root / "backend"
    legacy_reqs = backend_dir / "requirements.txt"
    if legacy_reqs.exists() or legacy_reqs.is_symlink():
        raise LockValidationError(
            "Unlocked legacy backend/requirements.txt must not coexist with backend/uv.lock"
        )

    pyproject_path = _require_regular_file(
        backend_dir / "pyproject.toml", root, "backend/pyproject.toml"
    )
    uv_lock_path = _require_regular_file(
        backend_dir / "uv.lock", root, "backend/uv.lock"
    )
    py_ver_path = _require_regular_file(
        backend_dir / ".python-version", root, "backend/.python-version"
    )

    try:
        py_ver = py_ver_path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise LockValidationError(
            "backend/.python-version must be valid UTF-8"
        ) from exc
    if not re.match(r"^3\.(?:1[2-9]|[2-9]\d)(?:\.\d+)?$", py_ver):
        raise LockValidationError(
            f"backend/.python-version must target Python >=3.12, got: {py_ver!r}"
        )

    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LockValidationError(f"Invalid backend/pyproject.toml: {exc}") from exc

    try:
        uv_lock = tomllib.loads(uv_lock_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LockValidationError(f"Invalid backend/uv.lock: {exc}") from exc

    build_system = pyproject.get("build-system")
    if not isinstance(build_system, dict):
        raise LockValidationError("backend/pyproject.toml must declare [build-system]")
    _parse_pinned_pep508_list(build_system.get("requires"), "[build-system].requires")
    backend_name = build_system.get("build-backend")
    if not isinstance(backend_name, str) or not backend_name.strip():
        raise LockValidationError(
            "[build-system].build-backend must be a non-empty string"
        )

    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise LockValidationError("backend/pyproject.toml must declare [project]")
    raw_name = project.get("name")
    raw_version = project.get("version")
    requires_python = project.get("requires-python")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise LockValidationError("[project].name must be a non-empty string")
    if not isinstance(raw_version, str) or not raw_version.strip():
        raise LockValidationError("[project].version must be a non-empty string")
    if not isinstance(requires_python, str) or not requires_python.startswith(">=3.12"):
        raise LockValidationError(
            f"[project].requires-python must require >=3.12, got: {requires_python!r}"
        )

    runtime_deps = _parse_pinned_pep508_list(
        project.get("dependencies"), "[project].dependencies"
    )
    dep_groups = pyproject.get("dependency-groups")
    if not isinstance(dep_groups, dict) or "dev" not in dep_groups:
        raise LockValidationError(
            "backend/pyproject.toml must declare [dependency-groups].dev"
        )
    dev_deps = _parse_pinned_pep508_list(
        dep_groups.get("dev"), "[dependency-groups].dev"
    )

    if uv_lock.get("version") != 1:
        raise LockValidationError(
            f"backend/uv.lock must use lock version 1, got: {uv_lock.get('version')!r}"
        )
    if uv_lock.get("requires-python") != requires_python:
        raise LockValidationError(
            "backend/uv.lock requires-python does not match backend/pyproject.toml"
        )

    packages = uv_lock.get("package")
    if not isinstance(packages, list) or not packages:
        raise LockValidationError("backend/uv.lock must contain [[package]] entries")

    project_norm = _normalize_pep503(raw_name)
    locked_versions: dict[str, set[str]] = {}
    root_pkg: dict[str, Any] | None = None

    for idx, pkg in enumerate(packages):
        if not isinstance(pkg, dict):
            raise LockValidationError(f"uv.lock package[{idx}] must be a table")
        pkg_name = pkg.get("name")
        pkg_version = pkg.get("version")
        source = pkg.get("source")
        if not isinstance(pkg_name, str) or not pkg_name:
            raise LockValidationError(f"uv.lock package[{idx}] is missing name")
        if not isinstance(pkg_version, str) or not pkg_version:
            raise LockValidationError(
                f"uv.lock package {pkg_name!r} is missing version"
            )
        if not isinstance(source, dict):
            raise LockValidationError(f"uv.lock package {pkg_name!r} is missing source")

        norm = _normalize_pep503(pkg_name)
        locked_versions.setdefault(norm, set()).add(pkg_version)

        if norm == project_norm:
            if source.get("editable") != "." and source.get("virtual") != ".":
                raise LockValidationError(
                    f"Root package {pkg_name!r} in uv.lock must use local editable/virtual source"
                )
            if pkg_version != raw_version:
                raise LockValidationError(
                    f"Root package version in uv.lock ({pkg_version!r}) != pyproject.toml ({raw_version!r})"
                )
            root_pkg = pkg
            continue

        if set(source.keys()) != {"registry"}:
            raise LockValidationError(
                f"External package {pkg_name!r} in uv.lock must resolve from an HTTPS registry, got {source!r}"
            )
        _require_https_url(
            source.get("registry"), f"uv.lock package {pkg_name!r} registry"
        )

        has_artifact = False
        if "sdist" in pkg:
            has_artifact = True
            _validate_uv_artifact(pkg["sdist"], f"uv.lock package {pkg_name!r} sdist")
        if "wheels" in pkg:
            wheels = pkg["wheels"]
            if not isinstance(wheels, list) or not wheels:
                raise LockValidationError(
                    f"uv.lock package {pkg_name!r} wheels must be a non-empty list"
                )
            has_artifact = True
            for w_idx, wheel in enumerate(wheels):
                _validate_uv_artifact(
                    wheel, f"uv.lock package {pkg_name!r} wheels[{w_idx}]"
                )
        if not has_artifact:
            raise LockValidationError(
                f"uv.lock package {pkg_name!r} must include hashed sdist or wheels"
            )

    if root_pkg is None:
        raise LockValidationError(
            f"Root package {project_norm!r} missing from backend/uv.lock"
        )

    metadata = root_pkg.get("metadata")
    if not isinstance(metadata, dict):
        raise LockValidationError(
            "Root package in backend/uv.lock is missing [package.metadata]"
        )
    locked_runtime_specs = _extract_uv_metadata_specs(
        metadata.get("requires-dist"), "package.metadata.requires-dist"
    )
    if locked_runtime_specs != runtime_deps:
        raise LockValidationError(
            "backend/pyproject.toml [project].dependencies drifted from backend/uv.lock"
        )

    requires_dev = metadata.get("requires-dev")
    if not isinstance(requires_dev, dict):
        raise LockValidationError(
            "Root package in backend/uv.lock is missing [package.metadata.requires-dev]"
        )
    locked_dev_specs = _extract_uv_metadata_specs(
        requires_dev.get("dev"), "package.metadata.requires-dev.dev"
    )
    if locked_dev_specs != dev_deps:
        raise LockValidationError(
            "backend/pyproject.toml [dependency-groups].dev drifted from backend/uv.lock"
        )

    for dep_name, dep_ver in {**runtime_deps, **dev_deps}.items():
        if dep_ver not in locked_versions.get(dep_name, set()):
            raise LockValidationError(
                f"Pinned dependency {dep_name}=={dep_ver} is not resolved in backend/uv.lock"
            )

    return len(packages)


def _parse_exact_npm_map(raw: object, section: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LockValidationError(
            f"{section} in frontend/package.json must be an object"
        )
    result: dict[str, str] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise LockValidationError(f"{section} contains an invalid package name")
        if not isinstance(spec, str) or EXACT_SEMVER_RE.match(spec) is None:
            raise LockValidationError(
                f"{section}[{name!r}] must use an exact semver pin, got: {spec!r}"
            )
        result[name] = spec
    return result


def validate_frontend_locks(root: Path) -> int:
    frontend_dir = root / "frontend"
    pkg_json_path = _require_regular_file(
        frontend_dir / "package.json", root, "frontend/package.json"
    )
    lock_json_path = _require_regular_file(
        frontend_dir / "package-lock.json", root, "frontend/package-lock.json"
    )

    try:
        pkg_json = json.loads(pkg_json_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LockValidationError(f"Invalid frontend/package.json: {exc}") from exc

    try:
        lock_json = json.loads(lock_json_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LockValidationError(f"Invalid frontend/package-lock.json: {exc}") from exc

    if not isinstance(pkg_json, dict):
        raise LockValidationError("frontend/package.json root must be an object")
    if not isinstance(lock_json, dict):
        raise LockValidationError("frontend/package-lock.json root must be an object")

    scripts = pkg_json.get("scripts")
    if not isinstance(scripts, dict):
        raise LockValidationError("frontend/package.json must declare scripts")
    for req_script in REQUIRED_FRONTEND_SCRIPTS:
        cmd = scripts.get(req_script)
        if not isinstance(cmd, str) or not cmd.strip():
            raise LockValidationError(
                f"frontend/package.json is missing required script {req_script!r}"
            )

    deps = _parse_exact_npm_map(pkg_json.get("dependencies"), "dependencies")
    dev_deps = _parse_exact_npm_map(pkg_json.get("devDependencies"), "devDependencies")
    if not deps:
        raise LockValidationError(
            "frontend/package.json dependencies must not be empty"
        )

    if lock_json.get("lockfileVersion") != 3:
        raise LockValidationError(
            f"frontend/package-lock.json must use lockfileVersion 3, got: {lock_json.get('lockfileVersion')!r}"
        )
    if lock_json.get("name") != pkg_json.get("name") or lock_json.get(
        "version"
    ) != pkg_json.get("version"):
        raise LockValidationError(
            "frontend/package-lock.json name/version does not match frontend/package.json"
        )

    packages = lock_json.get("packages")
    if not isinstance(packages, dict) or "" not in packages:
        raise LockValidationError(
            "frontend/package-lock.json must contain packages[''] root entry"
        )

    root_entry = packages[""]
    if not isinstance(root_entry, dict):
        raise LockValidationError(
            "frontend/package-lock.json packages[''] must be an object"
        )
    if (root_entry.get("dependencies") or {}) != deps:
        raise LockValidationError(
            "frontend/package.json dependencies drifted from frontend/package-lock.json"
        )
    if (root_entry.get("devDependencies") or {}) != dev_deps:
        raise LockValidationError(
            "frontend/package.json devDependencies drifted from frontend/package-lock.json"
        )

    for pkg_name, pkg_ver in {**deps, **dev_deps}.items():
        node_key = f"node_modules/{pkg_name}"
        locked_entry = packages.get(node_key)
        if not isinstance(locked_entry, dict):
            raise LockValidationError(
                f"Direct dependency {pkg_name!r} missing at {node_key!r} in package-lock.json"
            )
        if locked_entry.get("version") != pkg_ver:
            raise LockValidationError(
                f"Direct dependency {pkg_name!r} version mismatch: package.json has {pkg_ver!r}, lockfile has {locked_entry.get('version')!r}"
            )

    if "node_modules/vite" in packages:
        for platform_key in REQUIRED_VITE_PLATFORM_PACKAGES:
            if platform_key not in packages:
                raise LockValidationError(
                    f"frontend/package-lock.json is missing cross-platform optional entry {platform_key!r}"
                )

    resolved_count = 0
    for key, entry in packages.items():
        if key == "":
            continue
        if not isinstance(entry, dict):
            raise LockValidationError(
                f"frontend/package-lock.json packages[{key!r}] must be an object"
            )
        if entry.get("link") is True:
            raise LockValidationError(
                f"frontend/package-lock.json packages[{key!r}] must not use local link=true"
            )
        version = entry.get("version")
        if not isinstance(version, str) or not version.strip():
            raise LockValidationError(
                f"frontend/package-lock.json packages[{key!r}] is missing version"
            )
        _require_https_url(
            entry.get("resolved"),
            f"frontend/package-lock.json packages[{key!r}].resolved",
        )
        integrity = entry.get("integrity")
        if not isinstance(integrity, str) or SRI_INTEGRITY_RE.match(integrity) is None:
            raise LockValidationError(
                f"frontend/package-lock.json packages[{key!r}] is missing valid integrity digest"
            )
        resolved_count += 1

    if resolved_count == 0:
        raise LockValidationError("frontend/package-lock.json resolved zero packages")
    return resolved_count


def validate_repository_locks(root: Path = ROOT) -> LockSummary:
    if not isinstance(root, Path):
        raise LockValidationError("Repository root must be a pathlib.Path")
    if not root.exists() or not root.is_dir():
        raise LockValidationError(f"Repository root directory does not exist: {root}")
    backend_count = validate_backend_locks(root)
    frontend_count = validate_frontend_locks(root)
    return LockSummary(
        backend_packages=backend_count,
        frontend_packages=frontend_count,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate reproducible backend and frontend dependency lockfiles."
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="Repository root path containing backend/ and frontend/.",
    )
    args = parser.parse_args(argv)
    if not isinstance(args.root, str) or not args.root.strip():
        print(
            "Lockfile validation failed: --root must be a non-empty path",
            file=sys.stderr,
        )
        return 1
    try:
        summary = validate_repository_locks(Path(args.root))
    except LockValidationError as exc:
        print(f"Lockfile validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Lockfiles verified: backend={summary.backend_packages} packages, "
        f"frontend={summary.frontend_packages} packages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
