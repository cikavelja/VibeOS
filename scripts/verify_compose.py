from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_CONFIG_BYTES = 262_144

DURATION_RE = re.compile(r"^(?P<value>[1-9]\d*)(?:ms|s|m)$")
PINNED_TAG_RE = re.compile(r"^\d+(?:\.\d+)*(?:-[A-Za-z0-9._-]+)?$")
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
LOOPBACK_PORT_RE = re.compile(
    r"^127\.0\.0\.1:"
    r"(?P<host>\$\{[A-Z0-9_]+:-\d+\}):"
    r"(?P<container>\d+)(?:/tcp)?$"
)
FORBIDDEN_FLOATING_TAGS = frozenset(
    {"latest", "alpine", "slim", "edge", "nightly", "canary", "dev", "main", "master"}
)
ALLOWED_COMPOSE_TOP_KEYS = frozenset({"services", "volumes", "networks", "name"})
REQUIRED_SERVICES = frozenset({"postgres", "backend", "frontend"})


class ComposeValidationError(ValueError):
    """Raised when Docker Compose or Dockerfile configuration violates contracts."""


@dataclass(frozen=True)
class ComposeSummary:
    services: tuple[str, ...]
    dockerfiles: int


def _require_regular_file(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise ComposeValidationError(f"{label} must not be a symlink: {path}")
    if not path.exists() or not path.is_file():
        raise ComposeValidationError(f"Missing required {label}: {path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ComposeValidationError(
            f"{label} escapes repository root: {path}"
        ) from exc
    size = path.stat().st_size
    if size > MAX_CONFIG_BYTES:
        raise ComposeValidationError(
            f"{label} exceeds maximum size ({size} > {MAX_CONFIG_BYTES} bytes)"
        )
    return path


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ComposeValidationError(f"{label} must be valid UTF-8") from exc


def _strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or line[idx - 1].isspace():
                return line[:idx].rstrip()
    if in_single or in_double:
        raise ComposeValidationError(f"Unclosed quote in YAML line: {line!r}")
    return line.rstrip()


def _split_top_level(raw: str, sep: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    depth = 0
    for ch in raw:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_double:
            buf.append(ch)
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            continue
        if not in_single and not in_double:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth < 0:
                    raise ComposeValidationError(
                        f"Unbalanced brackets in YAML: {raw!r}"
                    )
            elif ch == sep and depth == 0:
                parts.append("".join(buf).strip())
                buf = []
                continue
        buf.append(ch)
    if in_single or in_double or depth != 0:
        raise ComposeValidationError(f"Malformed inline YAML expression: {raw!r}")
    parts.append("".join(buf).strip())
    return parts


def _split_key_value(text: str) -> tuple[str, str] | None:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if idx + 1 == len(text) or text[idx + 1].isspace():
                key = text[:idx].strip()
                val = text[idx + 1 :].strip()
                return key, val
    return None


def _parse_scalar(raw: str) -> Any:
    if not raw:
        return None
    if raw[0] in ("&", "*", "!"):
        raise ComposeValidationError(
            f"YAML anchors, aliases, and custom tags are forbidden: {raw!r}"
        )
    if raw.startswith('"'):
        if not raw.endswith('"') or len(raw) < 2:
            raise ComposeValidationError(f"Invalid double-quoted scalar: {raw!r}")
        inner = raw[1:-1]
        out: list[str] = []
        idx = 0
        while idx < len(inner):
            ch = inner[idx]
            if ch == "\\" and idx + 1 < len(inner):
                nxt = inner[idx + 1]
                escapes = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}
                out.append(escapes.get(nxt, nxt))
                idx += 2
            else:
                out.append(ch)
                idx += 1
        return "".join(out)
    if raw.startswith("'"):
        if not raw.endswith("'") or len(raw) < 2:
            raise ComposeValidationError(f"Invalid single-quoted scalar: {raw!r}")
        return raw[1:-1].replace("''", "'")
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "~"):
        return None
    if re.match(r"^-?\d+$", raw):
        return int(raw)
    return raw


def _parse_inline_value(raw: str) -> Any:
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise ComposeValidationError(f"Unclosed YAML flow sequence: {raw!r}")
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_inline_value(item) for item in _split_top_level(inner, ",")]
    if raw.startswith("{"):
        if not raw.endswith("}"):
            raise ComposeValidationError(f"Unclosed YAML flow mapping: {raw!r}")
        inner = raw[1:-1].strip()
        if not inner:
            return {}
        mapping: dict[str, Any] = {}
        for entry in _split_top_level(inner, ","):
            kv = _split_key_value(entry)
            if kv is None or not kv[0]:
                raise ComposeValidationError(f"Invalid flow mapping entry: {entry!r}")
            key = str(_parse_scalar(kv[0]))
            if key in mapping:
                raise ComposeValidationError(f"Duplicate YAML key {key!r}")
            mapping[key] = _parse_inline_value(kv[1])
        return mapping
    return _parse_scalar(raw)


def _parse_yaml_block(
    lines: list[tuple[int, int, str]], idx: int, min_indent: int
) -> tuple[Any, int]:
    if idx >= len(lines):
        return None, idx
    _, base_indent, first_text = lines[idx]
    if base_indent < min_indent:
        return None, idx

    if first_text == "-" or first_text.startswith("- "):
        items: list[Any] = []
        while idx < len(lines):
            line_no, indent, text = lines[idx]
            if indent < base_indent:
                break
            if indent > base_indent:
                raise ComposeValidationError(
                    f"Unexpected YAML indentation on line {line_no}"
                )
            if not (text == "-" or text.startswith("- ")):
                break
            remainder = text[1:].strip()
            idx += 1
            if not remainder:
                nested, idx = _parse_yaml_block(lines, idx, base_indent + 1)
                if nested is None:
                    raise ComposeValidationError(
                        f"Empty YAML sequence item on line {line_no}"
                    )
                items.append(nested)
            else:
                items.append(_parse_inline_value(remainder))
        return items, idx

    mapping: dict[str, Any] = {}
    while idx < len(lines):
        line_no, indent, text = lines[idx]
        if indent < base_indent:
            break
        if indent > base_indent:
            raise ComposeValidationError(
                f"Unexpected YAML indentation on line {line_no}"
            )
        if text == "-" or text.startswith("- "):
            raise ComposeValidationError(
                f"Unexpected sequence item in mapping on line {line_no}"
            )
        kv = _split_key_value(text)
        if kv is None or not kv[0]:
            raise ComposeValidationError(
                f"Invalid YAML mapping line {line_no}: {text!r}"
            )
        raw_key, raw_val = kv
        key = str(_parse_scalar(raw_key))
        if not key:
            raise ComposeValidationError(f"Empty YAML key on line {line_no}")
        if key in mapping:
            raise ComposeValidationError(
                f"Duplicate YAML key {key!r} on line {line_no}"
            )
        idx += 1
        if not raw_val:
            if idx < len(lines) and lines[idx][1] > base_indent:
                nested, idx = _parse_yaml_block(lines, idx, base_indent + 1)
                mapping[key] = nested
            else:
                mapping[key] = None
        else:
            mapping[key] = _parse_inline_value(raw_val)
    return mapping, idx


def parse_compose_yaml(content: str) -> dict[str, Any]:
    prepared: list[tuple[int, int, str]] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        if "\t" in raw_line.split("#", 1)[0]:
            leading = len(raw_line) - len(raw_line.lstrip(" \t"))
            if "\t" in raw_line[:leading]:
                raise ComposeValidationError(
                    f"Tab indentation is forbidden in docker-compose.yml (line {line_no})"
                )
        cleaned = _strip_yaml_comment(raw_line)
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        prepared.append((line_no, indent, cleaned.strip()))

    if not prepared:
        raise ComposeValidationError("docker-compose.yml must not be empty")
    parsed, next_idx = _parse_yaml_block(prepared, 0, 0)
    if next_idx != len(prepared) or not isinstance(parsed, dict):
        raise ComposeValidationError("docker-compose.yml root must be a YAML mapping")
    return parsed


def _validate_pinned_image(
    image: object, context: str, expected_repo_prefix: str | None = None
) -> None:
    if (
        not isinstance(image, str)
        or not image.strip()
        or any(ch.isspace() for ch in image)
    ):
        raise ComposeValidationError(f"{context} must specify a valid image reference")

    ref = image
    has_digest = False
    if "@" in ref:
        ref, digest = ref.split("@", 1)
        if SHA256_DIGEST_RE.match(digest) is None:
            raise ComposeValidationError(
                f"{context} digest must match sha256:<64 hex chars>, got: {image!r}"
            )
        has_digest = True

    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        repo = ref[:last_colon]
        tag = ref[last_colon + 1 :]
    else:
        repo = ref
        tag = ""

    if not repo:
        raise ComposeValidationError(f"{context} image repository is empty: {image!r}")
    if expected_repo_prefix is not None and repo != expected_repo_prefix:
        raise ComposeValidationError(
            f"{context} must use {expected_repo_prefix!r} image, got: {repo!r}"
        )
    if not tag and not has_digest:
        raise ComposeValidationError(
            f"{context} image must pin an explicit version tag: {image!r}"
        )
    if tag:
        if tag.lower() in FORBIDDEN_FLOATING_TAGS or PINNED_TAG_RE.match(tag) is None:
            raise ComposeValidationError(
                f"{context} image must pin a numeric version tag (not floating/latest), got: {image!r}"
            )


def _validate_loopback_ports(
    ports: object, service_name: str, expected_container_port: int
) -> None:
    if not isinstance(ports, list) or not ports:
        raise ComposeValidationError(
            f"services.{service_name}.ports must be a non-empty list"
        )
    matched_expected = False
    for entry in ports:
        if not isinstance(entry, str):
            raise ComposeValidationError(
                f"services.{service_name}.ports entries must be strings"
            )
        if not entry.startswith("127.0.0.1:"):
            raise ComposeValidationError(
                f"services.{service_name}.ports must bind exclusively to 127.0.0.1, got: {entry!r}"
            )
        match = LOOPBACK_PORT_RE.match(entry)
        if match is None:
            raise ComposeValidationError(
                f"services.{service_name}.ports must use parameterized loopback mapping "
                f"'127.0.0.1:${{VAR:-PORT}}:PORT', got: {entry!r}"
            )
        if int(match.group("container")) == expected_container_port:
            matched_expected = True
    if not matched_expected:
        raise ComposeValidationError(
            f"services.{service_name}.ports must expose container port {expected_container_port}"
        )


def _validate_healthcheck(
    hc: object, service_name: str, required_substrings: tuple[str, ...]
) -> None:
    if not isinstance(hc, dict):
        raise ComposeValidationError(
            f"services.{service_name} must declare a healthcheck mapping"
        )
    if hc.get("disable") is True:
        raise ComposeValidationError(
            f"services.{service_name}.healthcheck must not be disabled"
        )
    test_cmd = hc.get("test")
    cmd_text: str
    if isinstance(test_cmd, list):
        if len(test_cmd) < 2 or not all(
            isinstance(part, str) and part.strip() for part in test_cmd
        ):
            raise ComposeValidationError(
                f"services.{service_name}.healthcheck.test list must start with CMD/CMD-SHELL and command"
            )
        if test_cmd[0] not in ("CMD", "CMD-SHELL"):
            raise ComposeValidationError(
                f"services.{service_name}.healthcheck.test[0] must be CMD or CMD-SHELL, got: {test_cmd[0]!r}"
            )
        cmd_text = " ".join(test_cmd[1:])
    elif isinstance(test_cmd, str) and test_cmd.strip() and test_cmd.strip() != "NONE":
        cmd_text = test_cmd.strip()
    else:
        raise ComposeValidationError(
            f"services.{service_name}.healthcheck.test must define an active check"
        )

    for token in required_substrings:
        if token not in cmd_text:
            raise ComposeValidationError(
                f"services.{service_name}.healthcheck.test must check {token!r}"
            )

    for dur_field in ("interval", "timeout"):
        dur_val = hc.get(dur_field)
        if not isinstance(dur_val, str) or DURATION_RE.match(dur_val) is None:
            raise ComposeValidationError(
                f"services.{service_name}.healthcheck.{dur_field} must be a positive duration (e.g. '5s')"
            )

    retries = hc.get("retries")
    if type(retries) is not int or retries < 1:
        raise ComposeValidationError(
            f"services.{service_name}.healthcheck.retries must be a positive integer"
        )


def _validate_depends_on_healthy(
    service: dict[str, Any], service_name: str, upstream: str
) -> None:
    depends_on = service.get("depends_on")
    if not isinstance(depends_on, dict):
        raise ComposeValidationError(
            f"services.{service_name}.depends_on must be a mapping with condition: service_healthy"
        )
    dep_cfg = depends_on.get(upstream)
    if not isinstance(dep_cfg, dict) or dep_cfg.get("condition") != "service_healthy":
        raise ComposeValidationError(
            f"services.{service_name} must depend on {upstream!r} with condition: service_healthy"
        )


def _extract_env_map(service: dict[str, Any], service_name: str) -> dict[str, str]:
    raw_env = service.get("environment")
    if not isinstance(raw_env, dict) or not raw_env:
        raise ComposeValidationError(
            f"services.{service_name}.environment must be a non-empty mapping"
        )
    env_map: dict[str, str] = {}
    for key, val in raw_env.items():
        if not isinstance(key, str) or not key.strip():
            raise ComposeValidationError(
                f"services.{service_name}.environment contains an invalid key"
            )
        if not isinstance(val, (str, int)) or not str(val).strip():
            raise ComposeValidationError(
                f"services.{service_name}.environment.{key} must be a non-empty string"
            )
        env_map[key] = str(val)
    return env_map


def _validate_service_security_flags(
    service: dict[str, Any], service_name: str
) -> None:
    if service.get("privileged") is True:
        raise ComposeValidationError(
            f"services.{service_name} must not enable privileged: true"
        )
    if service.get("network_mode") == "host":
        raise ComposeValidationError(
            f"services.{service_name} must not use network_mode: host"
        )


def _validate_build_context(
    service: dict[str, Any], service_name: str, expected_context: str
) -> None:
    build_cfg = service.get("build")
    context_val: object = (
        build_cfg.get("context") if isinstance(build_cfg, dict) else build_cfg
    )
    if context_val != expected_context:
        raise ComposeValidationError(
            f"services.{service_name}.build must target {expected_context!r}, got: {context_val!r}"
        )


def validate_compose_file(root: Path) -> tuple[str, ...]:
    compose_path = _require_regular_file(
        root / "docker-compose.yml", root, "docker-compose.yml"
    )
    compose = parse_compose_yaml(_read_utf8(compose_path, "docker-compose.yml"))

    unknown_top = set(compose.keys()) - ALLOWED_COMPOSE_TOP_KEYS
    if unknown_top:
        raise ComposeValidationError(
            f"docker-compose.yml contains unsupported top-level keys: {sorted(unknown_top)}"
        )

    services = compose.get("services")
    if not isinstance(services, dict) or not services:
        raise ComposeValidationError("docker-compose.yml must define services")

    service_names = set(services.keys())
    missing = REQUIRED_SERVICES - service_names
    if missing:
        raise ComposeValidationError(
            f"docker-compose.yml is missing required services: {sorted(missing)}"
        )
    extra = service_names - REQUIRED_SERVICES
    if extra:
        raise ComposeValidationError(
            f"docker-compose.yml defines unexpected services outside scope: {sorted(extra)}"
        )

    volumes_top = compose.get("volumes")
    if not isinstance(volumes_top, dict) or not volumes_top:
        raise ComposeValidationError(
            "docker-compose.yml must declare top-level named volumes"
        )

    for svc_name in ("postgres", "backend", "frontend"):
        svc_obj = services.get(svc_name)
        if not isinstance(svc_obj, dict):
            raise ComposeValidationError(f"services.{svc_name} must be a mapping")
        _validate_service_security_flags(svc_obj, svc_name)

    postgres = services["postgres"]
    _validate_pinned_image(postgres.get("image"), "services.postgres.image", "postgres")
    pg_env = _extract_env_map(postgres, "postgres")
    for required_pg_var in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        if required_pg_var not in pg_env:
            raise ComposeValidationError(
                f"services.postgres.environment must declare {required_pg_var}"
            )
    _validate_loopback_ports(postgres.get("ports"), "postgres", 5432)
    pg_volumes = postgres.get("volumes")
    if not isinstance(pg_volumes, list) or not pg_volumes:
        raise ComposeValidationError(
            "services.postgres.volumes must mount a named volume for persistence"
        )
    has_pg_named_volume = False
    for mount in pg_volumes:
        if not isinstance(mount, str) or ":" not in mount:
            raise ComposeValidationError(
                f"services.postgres.volumes entry must be 'volume:/path', got: {mount!r}"
            )
        vol_name, target_path = mount.split(":", 1)
        if vol_name in volumes_top and target_path.startswith(
            "/var/lib/postgresql/data"
        ):
            has_pg_named_volume = True
    if not has_pg_named_volume:
        raise ComposeValidationError(
            "services.postgres.volumes must mount a declared named volume at /var/lib/postgresql/data"
        )
    _validate_healthcheck(
        postgres.get("healthcheck"), "postgres", ("pg_isready", "127.0.0.1")
    )

    backend = services["backend"]
    _validate_build_context(backend, "backend", "./backend")
    be_env = _extract_env_map(backend, "backend")
    db_url = be_env.get("DATABASE_URL", "")
    if "postgresql+psycopg://" not in db_url or "@postgres:5432/" not in db_url:
        raise ComposeValidationError(
            "services.backend.environment.DATABASE_URL must target postgresql+psycopg://...@postgres:5432/..."
        )
    _validate_loopback_ports(backend.get("ports"), "backend", 8000)
    _validate_depends_on_healthy(backend, "backend", "postgres")
    _validate_healthcheck(backend.get("healthcheck"), "backend", ("/api/v1/health",))

    frontend = services["frontend"]
    _validate_build_context(frontend, "frontend", "./frontend")
    fe_env = _extract_env_map(frontend, "frontend")
    if "http://backend:8000" not in fe_env.get("API_TARGET", ""):
        raise ComposeValidationError(
            "services.frontend.environment.API_TARGET must target http://backend:8000"
        )
    _validate_loopback_ports(frontend.get("ports"), "frontend", 5173)
    _validate_depends_on_healthy(frontend, "frontend", "backend")
    _validate_healthcheck(
        frontend.get("healthcheck"),
        "frontend",
        ("http://127.0.0.1:5173/", "http://127.0.0.1:5173/api/v1/health"),
    )

    return ("postgres", "backend", "frontend")


def _parse_dockerfile_instructions(content: str) -> list[tuple[str, str]]:
    logical_lines: list[str] = []
    current: list[str] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or (stripped.startswith("#") and not current):
            continue
        if stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current.append(stripped[:-1].strip())
        else:
            current.append(stripped)
            logical_lines.append(" ".join(part for part in current if part))
            current = []
    if current:
        logical_lines.append(" ".join(part for part in current if part))

    instructions: list[tuple[str, str]] = []
    for line in logical_lines:
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ComposeValidationError(f"Malformed Dockerfile instruction: {line!r}")
        instructions.append((parts[0].upper(), parts[1].strip()))
    return instructions


def _validate_dockerfile(
    path: Path,
    root: Path,
    label: str,
    expected_base_repo: str,
    expected_port: int,
    required_run_fragment: str,
) -> None:
    _require_regular_file(path, root, label)
    content = _read_utf8(path, label)
    instructions = _parse_dockerfile_instructions(content)
    if not instructions or instructions[0][0] != "FROM":
        raise ComposeValidationError(f"{label} must begin with a FROM instruction")

    stage_user: str | None = None
    has_healthcheck = False
    has_cmd_or_entrypoint = False
    exposed_ports: set[str] = set()
    run_commands: list[str] = []

    for verb, args in instructions:
        if verb == "FROM":
            tokens = [tok for tok in args.split() if not tok.startswith("--")]
            if not tokens:
                raise ComposeValidationError(f"{label} has empty FROM instruction")
            _validate_pinned_image(tokens[0], f"{label} FROM", expected_base_repo)
            stage_user = None
            has_healthcheck = False
            has_cmd_or_entrypoint = False
            exposed_ports = set()
        elif verb == "USER":
            stage_user = args.strip()
        elif verb == "EXPOSE":
            for tok in args.split():
                exposed_ports.add(tok.split("/", 1)[0])
        elif verb == "HEALTHCHECK":
            if args.upper() == "NONE":
                raise ComposeValidationError(f"{label} must not disable HEALTHCHECK")
            if "/api/v1/health" not in args:
                raise ComposeValidationError(
                    f"{label} HEALTHCHECK must probe /api/v1/health"
                )
            has_healthcheck = True
        elif verb in ("CMD", "ENTRYPOINT"):
            has_cmd_or_entrypoint = True
        elif verb == "RUN":
            run_commands.append(args)

    if stage_user is None:
        raise ComposeValidationError(
            f"{label} final stage must set a non-root USER instruction"
        )
    primary_user = stage_user.split(":", 1)[0].strip().lower()
    if not primary_user or primary_user in ("root", "0"):
        raise ComposeValidationError(
            f"{label} final stage must not run as root USER, got: {stage_user!r}"
        )
    if str(expected_port) not in exposed_ports:
        raise ComposeValidationError(f"{label} must EXPOSE {expected_port}")
    if not has_healthcheck:
        raise ComposeValidationError(f"{label} must declare a HEALTHCHECK instruction")
    if not has_cmd_or_entrypoint:
        raise ComposeValidationError(f"{label} must declare CMD or ENTRYPOINT")
    if not any(required_run_fragment in cmd for cmd in run_commands):
        raise ComposeValidationError(
            f"{label} must install locked dependencies via {required_run_fragment!r}"
        )


def _validate_dockerignore(
    path: Path, root: Path, label: str, required_entries: tuple[str, ...]
) -> None:
    _require_regular_file(path, root, label)
    entries = {
        line.strip().rstrip("/")
        for line in _read_utf8(path, label).splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for req in required_entries:
        if req not in entries:
            raise ComposeValidationError(f"{label} must exclude {req!r}")


def validate_repository_compose(root: Path = ROOT) -> ComposeSummary:
    if not isinstance(root, Path):
        raise ComposeValidationError("Repository root must be a pathlib.Path")
    if not root.exists() or not root.is_dir():
        raise ComposeValidationError(
            f"Repository root directory does not exist: {root}"
        )

    services = validate_compose_file(root)
    _validate_dockerfile(
        root / "backend" / "Dockerfile",
        root,
        "backend/Dockerfile",
        expected_base_repo="python",
        expected_port=8000,
        required_run_fragment="uv sync --locked",
    )
    _validate_dockerfile(
        root / "frontend" / "Dockerfile",
        root,
        "frontend/Dockerfile",
        expected_base_repo="node",
        expected_port=5173,
        required_run_fragment="npm ci",
    )
    _validate_dockerignore(
        root / "backend" / ".dockerignore",
        root,
        "backend/.dockerignore",
        (".venv", "*.db"),
    )
    _validate_dockerignore(
        root / "frontend" / ".dockerignore",
        root,
        "frontend/.dockerignore",
        ("node_modules", "dist"),
    )
    return ComposeSummary(services=services, dockerfiles=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Docker Compose and Dockerfile development contracts."
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="Repository root path containing docker-compose.yml, backend/, and frontend/.",
    )
    args = parser.parse_args(argv)
    if not isinstance(args.root, str) or not args.root.strip():
        print(
            "Compose validation failed: --root must be a non-empty path",
            file=sys.stderr,
        )
        return 1
    try:
        summary = validate_repository_compose(Path(args.root))
    except ComposeValidationError as exc:
        print(f"Compose validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Compose verified: services={','.join(summary.services)}, "
        f"dockerfiles={summary.dockerfiles}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
