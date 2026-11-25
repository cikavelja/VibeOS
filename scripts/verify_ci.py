from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_WORKFLOW_BYTES = 262_144
MAX_TIMEOUT_MINUTES = 60
MAX_COMMAND_TIMEOUT_SECONDS = 3600.0

REQUIRED_WORKFLOW_FILES = ("planning.yml", "product-preview.yml")
ALLOWED_WORKFLOW_TOP_KEYS = frozenset(
    {"name", "on", "permissions", "concurrency", "env", "defaults", "jobs"}
)
ALLOWED_JOB_KEYS = frozenset(
    {
        "name",
        "runs-on",
        "timeout-minutes",
        "permissions",
        "defaults",
        "env",
        "needs",
        "if",
        "services",
        "strategy",
        "continue-on-error",
        "steps",
    }
)
ALLOWED_STEP_KEYS = frozenset(
    {
        "id",
        "name",
        "if",
        "uses",
        "with",
        "run",
        "working-directory",
        "shell",
        "env",
        "continue-on-error",
        "timeout-minutes",
    }
)
BLOCK_SCALAR_HEADERS = frozenset({"|", "|-", "|+", ">", ">-", ">+"})
PINNED_ACTION_RE = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@(?:v\d+(?:\.\d+)*|[0-9a-f]{40})$"
)
EXIT_MASKING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\|\|\s*(?:true|:|exit\s+0)(?:\s|$|;|#|\))"),
        "exit-code masking ('|| true' / '|| :' / '|| exit 0')",
    ),
    (
        re.compile(r"(?:^|[;\n])\s*set\s+\+e(?:\s|$|;|#)"),
        "disabling shell errexit ('set +e')",
    ),
    (
        re.compile(r";\s*true\s*(?:$|\n|#)"),
        "trailing '; true' exit-code override",
    ),
    (
        re.compile(r"--collect-only\b"),
        "pytest '--collect-only' dry-run flag",
    ),
    (
        re.compile(r"--passWithNoTests\b"),
        "test runner '--passWithNoTests' bypass flag",
    ),
)
FORBIDDEN_TRIGGERS = frozenset({"pull_request_target", "workflow_run"})


class CIValidationError(ValueError):
    """Raised when GitHub Actions CI workflows or gate specs violate contracts."""


@dataclass(frozen=True)
class CISummary:
    workflows: tuple[str, ...]
    jobs: int


@dataclass(frozen=True)
class GateCommandSpec:
    name: str
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float = 300.0
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class GateStepResult:
    name: str
    argv: tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class GateRunReport:
    passed: bool
    exit_code: int
    results: tuple[GateStepResult, ...]


def _require_safe_dir(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise CIValidationError(f"{label} must not be a symlink: {path}")
    if not path.exists() or not path.is_dir():
        raise CIValidationError(f"Missing required {label}: {path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise CIValidationError(f"{label} escapes repository root: {path}") from exc
    return path


def _require_regular_file(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise CIValidationError(f"{label} must not be a symlink: {path}")
    if not path.exists() or not path.is_file():
        raise CIValidationError(f"Missing required {label}: {path}")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise CIValidationError(f"{label} escapes repository root: {path}") from exc
    size = path.stat().st_size
    if size > MAX_WORKFLOW_BYTES:
        raise CIValidationError(
            f"{label} exceeds maximum size ({size} > {MAX_WORKFLOW_BYTES} bytes)"
        )
    return path


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CIValidationError(f"{label} must be valid UTF-8") from exc


def _strip_yaml_comment(line: str, label: str, line_no: int) -> str:
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
        raise CIValidationError(
            f"Unclosed quote in {label} on line {line_no}: {line!r}"
        )
    return line.rstrip()


def _split_top_level(raw: str, sep: str, label: str) -> list[str]:
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
                    raise CIValidationError(f"Unbalanced brackets in {label}: {raw!r}")
            elif ch == sep and depth == 0:
                parts.append("".join(buf).strip())
                buf = []
                continue
        buf.append(ch)
    if in_single or in_double or depth != 0:
        raise CIValidationError(f"Malformed inline YAML expression in {label}: {raw!r}")
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
                return text[:idx].strip(), text[idx + 1 :].strip()
    return None


def _parse_scalar(raw: str, label: str) -> Any:
    if not raw:
        return None
    if raw[0] in ("&", "*", "!"):
        raise CIValidationError(
            f"YAML anchors, aliases, and custom tags are forbidden in {label}: {raw!r}"
        )
    if raw.startswith('"'):
        if not raw.endswith('"') or len(raw) < 2:
            raise CIValidationError(f"Invalid double-quoted scalar in {label}: {raw!r}")
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
            raise CIValidationError(f"Invalid single-quoted scalar in {label}: {raw!r}")
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


def _parse_inline_value(raw: str, label: str) -> Any:
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise CIValidationError(f"Unclosed YAML flow sequence in {label}: {raw!r}")
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [
            _parse_inline_value(item, label)
            for item in _split_top_level(inner, ",", label)
        ]
    if raw.startswith("{"):
        if not raw.endswith("}"):
            raise CIValidationError(f"Unclosed YAML flow mapping in {label}: {raw!r}")
        inner = raw[1:-1].strip()
        if not inner:
            return {}
        mapping: dict[str, Any] = {}
        for entry in _split_top_level(inner, ",", label):
            kv = _split_key_value(entry)
            if kv is None or not kv[0]:
                raise CIValidationError(
                    f"Invalid flow mapping entry in {label}: {entry!r}"
                )
            key = str(_parse_scalar(kv[0], label))
            if key in mapping:
                raise CIValidationError(f"Duplicate YAML key {key!r} in {label}")
            mapping[key] = _parse_inline_value(kv[1], label)
        return mapping
    return _parse_scalar(raw, label)


class _WorkflowYamlParser:
    def __init__(self, content: str, label: str) -> None:
        self._raw_lines = content.splitlines()
        self._label = label

    def parse(self) -> dict[str, Any]:
        idx = self._next_yaml_line(0)
        if idx >= len(self._raw_lines):
            raise CIValidationError(f"{self._label} must not be empty")
        parsed, next_idx = self._parse_block(idx, 0)
        trailing = self._next_yaml_line(next_idx)
        if trailing < len(self._raw_lines) or not isinstance(parsed, dict):
            raise CIValidationError(f"{self._label} root must be a YAML mapping")
        return parsed

    def _inspect_yaml_line(self, idx: int) -> tuple[int, str] | None:
        raw_line = self._raw_lines[idx]
        line_no = idx + 1
        leading_len = len(raw_line) - len(raw_line.lstrip(" \t"))
        if "\t" in raw_line[:leading_len]:
            raise CIValidationError(
                f"Tab indentation is forbidden in {self._label} (line {line_no})"
            )
        cleaned = _strip_yaml_comment(raw_line, self._label, line_no)
        if not cleaned.strip():
            return None
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        return indent, cleaned.strip()

    def _next_yaml_line(self, start_idx: int) -> int:
        idx = start_idx
        while idx < len(self._raw_lines):
            if self._inspect_yaml_line(idx) is not None:
                return idx
            idx += 1
        return idx

    def _read_block_scalar(
        self, start_idx: int, key_indent: int, header: str, header_line_no: int
    ) -> tuple[str, int]:
        collected: list[str] = []
        scalar_indent: int | None = None
        idx = start_idx
        while idx < len(self._raw_lines):
            raw_line = self._raw_lines[idx]
            line_no = idx + 1
            if not raw_line.strip():
                if "\t" in raw_line and scalar_indent is None:
                    raise CIValidationError(
                        f"Tab indentation is forbidden in {self._label} (line {line_no})"
                    )
                collected.append("")
                idx += 1
                continue

            leading_ws = len(raw_line) - len(raw_line.lstrip(" \t"))
            if "\t" in raw_line[:leading_ws]:
                raise CIValidationError(
                    f"Tab indentation is forbidden in {self._label} (line {line_no})"
                )
            spaces = len(raw_line) - len(raw_line.lstrip(" "))
            if scalar_indent is None:
                if spaces <= key_indent:
                    break
                scalar_indent = spaces
            elif spaces < scalar_indent:
                if spaces > key_indent:
                    raise CIValidationError(
                        f"Inconsistent block scalar indentation in {self._label} (line {line_no})"
                    )
                break

            collected.append(raw_line[scalar_indent:])
            idx += 1

        while collected and collected[-1] == "":
            collected.pop()
        if scalar_indent is None or not collected:
            raise CIValidationError(
                f"Empty YAML block scalar in {self._label} (line {header_line_no})"
            )
        if header.startswith(">"):
            text = " ".join(part for part in collected if part)
        else:
            text = "\n".join(collected)
        if header.endswith("+"):
            text += "\n"
        return text, idx

    def _resolve_mapping_value(
        self, raw_val: str, next_idx: int, key_indent: int, line_no: int
    ) -> tuple[Any, int]:
        if raw_val in BLOCK_SCALAR_HEADERS:
            return self._read_block_scalar(next_idx, key_indent, raw_val, line_no)
        if not raw_val:
            peek = self._next_yaml_line(next_idx)
            if peek < len(self._raw_lines):
                info = self._inspect_yaml_line(peek)
                if info is not None and info[0] > key_indent:
                    return self._parse_block(peek, key_indent + 1)
            return None, peek
        return _parse_inline_value(raw_val, self._label), next_idx

    def _parse_mapping_entries(
        self, idx: int, target_indent: int, mapping: dict[str, Any]
    ) -> int:
        while True:
            idx = self._next_yaml_line(idx)
            if idx >= len(self._raw_lines):
                break
            info = self._inspect_yaml_line(idx)
            assert info is not None
            indent, text = info
            line_no = idx + 1
            if indent < target_indent:
                break
            if indent > target_indent:
                raise CIValidationError(
                    f"Unexpected YAML indentation in {self._label} on line {line_no}"
                )
            if text == "-" or text.startswith("- "):
                raise CIValidationError(
                    f"Unexpected sequence item in mapping in {self._label} on line {line_no}"
                )
            kv = _split_key_value(text)
            if kv is None or not kv[0]:
                raise CIValidationError(
                    f"Invalid YAML mapping in {self._label} on line {line_no}: {text!r}"
                )
            key = str(_parse_scalar(kv[0], self._label))
            if not key:
                raise CIValidationError(
                    f"Empty YAML key in {self._label} on line {line_no}"
                )
            if key in mapping:
                raise CIValidationError(
                    f"Duplicate YAML key {key!r} in {self._label} on line {line_no}"
                )
            val, idx = self._resolve_mapping_value(
                kv[1], idx + 1, target_indent, line_no
            )
            mapping[key] = val
        return idx

    def _parse_block(self, start_idx: int, min_indent: int) -> tuple[Any, int]:
        idx = self._next_yaml_line(start_idx)
        if idx >= len(self._raw_lines):
            return None, idx
        first_info = self._inspect_yaml_line(idx)
        assert first_info is not None
        base_indent, first_text = first_info
        if base_indent < min_indent:
            return None, idx

        if first_text == "-" or first_text.startswith("- "):
            items: list[Any] = []
            while True:
                idx = self._next_yaml_line(idx)
                if idx >= len(self._raw_lines):
                    break
                info = self._inspect_yaml_line(idx)
                assert info is not None
                indent, text = info
                line_no = idx + 1
                if indent < base_indent:
                    break
                if indent > base_indent:
                    raise CIValidationError(
                        f"Unexpected YAML indentation in {self._label} on line {line_no}"
                    )
                if not (text == "-" or text.startswith("- ")):
                    break

                after_dash = text[1:]
                spaces_after_dash = len(after_dash) - len(after_dash.lstrip(" "))
                remainder = after_dash.strip()
                if not remainder:
                    nested, idx = self._parse_block(idx + 1, base_indent + 1)
                    if nested is None:
                        raise CIValidationError(
                            f"Empty YAML sequence item in {self._label} on line {line_no}"
                        )
                    items.append(nested)
                    continue

                inline_kv = (
                    None
                    if remainder.startswith(("{", "["))
                    else _split_key_value(remainder)
                )
                if inline_kv is not None and inline_kv[0]:
                    item_indent = base_indent + 1 + spaces_after_dash
                    first_key = str(_parse_scalar(inline_kv[0], self._label))
                    if not first_key:
                        raise CIValidationError(
                            f"Empty YAML key in {self._label} on line {line_no}"
                        )
                    first_val, next_idx = self._resolve_mapping_value(
                        inline_kv[1], idx + 1, item_indent, line_no
                    )
                    item_map: dict[str, Any] = {first_key: first_val}
                    peek = self._next_yaml_line(next_idx)
                    if peek < len(self._raw_lines):
                        peek_info = self._inspect_yaml_line(peek)
                        assert peek_info is not None
                        if peek_info[0] > base_indent:
                            if peek_info[0] != item_indent:
                                raise CIValidationError(
                                    f"Unexpected YAML indentation in {self._label} on line {peek + 1}"
                                )
                            next_idx = self._parse_mapping_entries(
                                peek, item_indent, item_map
                            )
                    items.append(item_map)
                    idx = next_idx
                else:
                    items.append(_parse_inline_value(remainder, self._label))
                    idx += 1
            return items, idx

        mapping: dict[str, Any] = {}
        idx = self._parse_mapping_entries(idx, base_indent, mapping)
        return mapping, idx


def parse_workflow_yaml(content: str, label: str = "workflow.yml") -> dict[str, Any]:
    return _WorkflowYamlParser(content, label).parse()


def _check_no_exit_code_masking(command_text: str, context: str) -> None:
    for pattern, description in EXIT_MASKING_PATTERNS:
        if pattern.search(command_text) is not None:
            raise CIValidationError(f"{context} must not use {description}")


def _validate_triggers(raw_on: object, label: str) -> None:
    if isinstance(raw_on, list):
        if not all(isinstance(item, str) and item.strip() for item in raw_on):
            raise CIValidationError(f"{label} 'on' list entries must be strings")
        trigger_names = set(raw_on)
        if FORBIDDEN_TRIGGERS & trigger_names:
            bad = sorted(FORBIDDEN_TRIGGERS & trigger_names)
            raise CIValidationError(
                f"{label} must not enable dangerous triggers: {bad}"
            )
        missing = {"push", "pull_request"} - trigger_names
        if missing:
            raise CIValidationError(
                f"{label} 'on' must include push and pull_request, missing: {sorted(missing)}"
            )
        return

    if isinstance(raw_on, dict):
        trigger_names = set(raw_on.keys())
        if FORBIDDEN_TRIGGERS & trigger_names:
            bad = sorted(FORBIDDEN_TRIGGERS & trigger_names)
            raise CIValidationError(
                f"{label} must not enable dangerous triggers: {bad}"
            )
        missing = {"push", "pull_request"} - trigger_names
        if missing:
            raise CIValidationError(
                f"{label} 'on' must include push and pull_request, missing: {sorted(missing)}"
            )
        for event_name in ("push", "pull_request"):
            cfg = raw_on.get(event_name)
            if cfg is None:
                continue
            if not isinstance(cfg, dict):
                raise CIValidationError(
                    f"{label} 'on.{event_name}' must be null or a mapping"
                )
            for bypass_key in ("paths-ignore", "branches-ignore"):
                if bypass_key in cfg:
                    raise CIValidationError(
                        f"{label} 'on.{event_name}' must not bypass checks with {bypass_key}"
                    )
            if "branches" in cfg:
                branches = cfg["branches"]
                if not isinstance(branches, list) or "main" not in branches:
                    raise CIValidationError(
                        f"{label} 'on.{event_name}.branches' must include 'main'"
                    )
        return

    raise CIValidationError(
        f"{label} must declare 'on' with both push and pull_request triggers"
    )


def _validate_permissions(raw_perms: object, context: str, require_exact: bool) -> None:
    if not isinstance(raw_perms, dict) or not raw_perms:
        raise CIValidationError(
            f"{context} must explicitly declare least-privilege 'permissions: contents: read'"
        )
    for scope, level in raw_perms.items():
        if scope != "contents" or level not in ("read", "none"):
            raise CIValidationError(
                f"{context} exceeds least-privilege 'contents: read' ({scope!r}: {level!r})"
            )
    if require_exact and raw_perms != {"contents": "read"}:
        raise CIValidationError(
            f"{context} must equal {{'contents': 'read'}}, got: {raw_perms!r}"
        )


def _validate_concurrency(raw_conc: object, label: str) -> None:
    if not isinstance(raw_conc, dict):
        raise CIValidationError(f"{label} must declare top-level 'concurrency' mapping")
    group = raw_conc.get("group")
    if not isinstance(group, str) or "${{" not in group or "}}" not in group:
        raise CIValidationError(
            f"{label} concurrency.group must be a dynamic '${{{{ ... }}}}' expression"
        )
    cancel = raw_conc.get("cancel-in-progress")
    if cancel is True:
        return
    if (
        isinstance(cancel, str)
        and "${{" in cancel
        and ("pull_request" in cancel or "github.ref" in cancel)
    ):
        return
    raise CIValidationError(
        f"{label} concurrency.cancel-in-progress must cancel superseded PR runs"
    )


@dataclass(frozen=True)
class _ParsedJobSteps:
    working_directory: str | None
    unconditional_uses: tuple[str, ...]
    unconditional_runs: tuple[str, ...]
    all_steps: tuple[dict[str, Any], ...]


def _validate_and_collect_job(
    job_id: str, job_cfg: object, label: str
) -> _ParsedJobSteps:
    ctx = f"{label} jobs.{job_id}"
    if not isinstance(job_cfg, dict):
        raise CIValidationError(f"{ctx} must be a mapping")

    unknown = set(job_cfg.keys()) - ALLOWED_JOB_KEYS
    if unknown:
        raise CIValidationError(f"{ctx} contains unsupported keys: {sorted(unknown)}")

    if (
        job_cfg.get("continue-on-error") is not None
        and job_cfg.get("continue-on-error") is not False
    ):
        raise CIValidationError(f"{ctx} must not set continue-on-error")

    if "if" in job_cfg:
        raise CIValidationError(
            f"{ctx} must not conditionally skip execution via job-level 'if'"
        )

    if "permissions" in job_cfg:
        _validate_permissions(
            job_cfg["permissions"], f"{ctx}.permissions", require_exact=False
        )

    runs_on = job_cfg.get("runs-on")
    if not isinstance(runs_on, str) or not runs_on.strip():
        raise CIValidationError(f"{ctx} must declare a non-empty 'runs-on' runner")

    timeout = job_cfg.get("timeout-minutes")
    if type(timeout) is not int or timeout < 1 or timeout > MAX_TIMEOUT_MINUTES:
        raise CIValidationError(
            f"{ctx}.timeout-minutes must be an integer between 1 and {MAX_TIMEOUT_MINUTES}"
        )

    working_dir: str | None = None
    defaults = job_cfg.get("defaults")
    if defaults is not None:
        if not isinstance(defaults, dict):
            raise CIValidationError(f"{ctx}.defaults must be a mapping")
        run_defaults = defaults.get("run")
        if run_defaults is not None:
            if not isinstance(run_defaults, dict):
                raise CIValidationError(f"{ctx}.defaults.run must be a mapping")
            wd = run_defaults.get("working-directory")
            if wd is not None:
                if not isinstance(wd, str) or not wd.strip():
                    raise CIValidationError(
                        f"{ctx}.defaults.run.working-directory must be a non-empty string"
                    )
                working_dir = wd.strip()

    steps = job_cfg.get("steps")
    if not isinstance(steps, list) or not steps:
        raise CIValidationError(f"{ctx}.steps must be a non-empty list")

    unconditional_uses: list[str] = []
    unconditional_runs: list[str] = []
    validated_steps: list[dict[str, Any]] = []

    for idx, step in enumerate(steps):
        step_ctx = f"{ctx}.steps[{idx}]"
        if not isinstance(step, dict):
            raise CIValidationError(f"{step_ctx} must be a mapping")
        unknown_step = set(step.keys()) - ALLOWED_STEP_KEYS
        if unknown_step:
            raise CIValidationError(
                f"{step_ctx} contains unsupported keys: {sorted(unknown_step)}"
            )

        if (
            step.get("continue-on-error") is not None
            and step.get("continue-on-error") is not False
        ):
            raise CIValidationError(
                f"{step_ctx} must not enable continue-on-error: {step.get('continue-on-error')!r}"
            )

        has_uses = "uses" in step
        has_run = "run" in step
        if has_uses == has_run:
            raise CIValidationError(
                f"{step_ctx} must declare exactly one of 'uses' or 'run'"
            )

        step_if = step.get("if")
        if step_if is not None:
            if step_if is False or not isinstance(step_if, str) or not step_if.strip():
                raise CIValidationError(
                    f"{step_ctx}.if must be a non-empty condition string, got: {step_if!r}"
                )
            lowered_if = step_if.strip().lower()
            if lowered_if in ("false", "${{ false }}"):
                raise CIValidationError(f"{step_ctx} must not disable execution")

        if has_uses:
            uses_val = step["uses"]
            if (
                not isinstance(uses_val, str)
                or PINNED_ACTION_RE.match(uses_val) is None
            ):
                raise CIValidationError(
                    f"{step_ctx}.uses must pin an explicit version tag or commit SHA, got: {uses_val!r}"
                )
            if step_if is None:
                unconditional_uses.append(uses_val)
        else:
            run_val = step["run"]
            if not isinstance(run_val, str) or not run_val.strip():
                raise CIValidationError(f"{step_ctx}.run must be a non-empty string")
            _check_no_exit_code_masking(run_val, step_ctx)
            if step_if is None:
                unconditional_runs.append(run_val.strip())

        validated_steps.append(step)

    return _ParsedJobSteps(
        working_directory=working_dir,
        unconditional_uses=tuple(unconditional_uses),
        unconditional_runs=tuple(unconditional_runs),
        all_steps=tuple(validated_steps),
    )


def _require_action_prefix(uses_list: Sequence[str], prefix: str, context: str) -> None:
    if not any(entry.startswith(prefix) for entry in uses_list):
        raise CIValidationError(f"{context} must include unconditional {prefix!r} step")


def _require_run_predicate(
    runs: Sequence[str],
    predicate: Any,
    description: str,
    context: str,
) -> None:
    if not any(predicate(cmd) for cmd in runs):
        raise CIValidationError(
            f"{context} is missing mandatory unconditional quality gate: {description}"
        )


def _validate_product_preview_jobs(
    jobs: Mapping[str, _ParsedJobSteps], label: str
) -> None:
    for req_job in ("backend", "frontend", "docker-compose"):
        if req_job not in jobs:
            raise CIValidationError(f"{label} must define required job {req_job!r}")

    backend = jobs["backend"]
    if backend.working_directory != "backend":
        raise CIValidationError(
            f"{label} jobs.backend must set defaults.run.working-directory to 'backend'"
        )
    _require_action_prefix(
        backend.unconditional_uses, "actions/checkout@", f"{label} jobs.backend"
    )
    _require_action_prefix(
        backend.unconditional_uses, "actions/setup-python@", f"{label} jobs.backend"
    )
    backend_gates: tuple[tuple[str, Any], ...] = (
        ("uv sync --locked", lambda c: "uv sync --locked" in c),
        ("uv build", lambda c: re.search(r"\buv\s+build\b", c) is not None),
        (
            "uv run ruff check .",
            lambda c: re.search(r"\buv\s+run\s+ruff\s+check\s+\.", c) is not None,
        ),
        (
            "uv run ruff format --check .",
            lambda c: (
                re.search(r"\buv\s+run\s+ruff\s+format\s+--check\s+\.", c) is not None
            ),
        ),
        (
            "uv run mypy --strict app tests",
            lambda c: (
                re.search(r"\buv\s+run\s+mypy\s+--strict\s+app\s+tests\b", c)
                is not None
            ),
        ),
        (
            "uv run pytest",
            lambda c: (
                re.search(r"\buv\s+run\s+pytest(?:\s+-[qv]+)?\s*$", c) is not None
            ),
        ),
    )
    for desc, pred in backend_gates:
        _require_run_predicate(
            backend.unconditional_runs, pred, desc, f"{label} jobs.backend"
        )

    frontend = jobs["frontend"]
    if frontend.working_directory != "frontend":
        raise CIValidationError(
            f"{label} jobs.frontend must set defaults.run.working-directory to 'frontend'"
        )
    _require_action_prefix(
        frontend.unconditional_uses, "actions/checkout@", f"{label} jobs.frontend"
    )
    _require_action_prefix(
        frontend.unconditional_uses, "actions/setup-node@", f"{label} jobs.frontend"
    )
    frontend_gates: tuple[tuple[str, Any], ...] = (
        ("npm ci", lambda c: re.search(r"\bnpm\s+ci\b", c) is not None),
        ("npm run lint", lambda c: re.search(r"\bnpm\s+run\s+lint\b", c) is not None),
        (
            "npm run typecheck",
            lambda c: re.search(r"\bnpm\s+run\s+typecheck\b", c) is not None,
        ),
        ("npm run build", lambda c: re.search(r"\bnpm\s+run\s+build\b", c) is not None),
        (
            "npm run test / npm test",
            lambda c: re.search(r"\bnpm\s+(?:run\s+)?test\b", c) is not None,
        ),
        (
            "playwright install chromium",
            lambda c: "playwright install" in c and "chromium" in c,
        ),
        (
            "npm run e2e / npx playwright test",
            lambda c: (
                re.search(r"\b(?:npm\s+run\s+e2e|npx\s+playwright\s+test)\b", c)
                is not None
            ),
        ),
    )
    for desc, pred in frontend_gates:
        _require_run_predicate(
            frontend.unconditional_runs, pred, desc, f"{label} jobs.frontend"
        )

    compose = jobs["docker-compose"]
    _require_action_prefix(
        compose.unconditional_uses,
        "actions/checkout@",
        f"{label} jobs.docker-compose",
    )
    compose_gates: tuple[tuple[str, Any], ...] = (
        ("scripts/verify_compose.py", lambda c: "scripts/verify_compose.py" in c),
        (
            "docker compose up -d --build --wait",
            lambda c: "docker compose up" in c and "--build" in c and "--wait" in c,
        ),
        (
            "HTTP health checks (:8000/api/v1/health and :5173/api/v1/health)",
            lambda c: (
                "127.0.0.1:8000/api/v1/health" in c
                and "127.0.0.1:5173/api/v1/health" in c
            ),
        ),
    )
    for desc, pred in compose_gates:
        _require_run_predicate(
            compose.unconditional_runs, pred, desc, f"{label} jobs.docker-compose"
        )

    has_always_teardown = any(
        isinstance(step.get("run"), str)
        and "docker compose down -v --remove-orphans" in step["run"]
        and step.get("if") == "always()"
        for step in compose.all_steps
    )
    if not has_always_teardown:
        raise CIValidationError(
            f"{label} jobs.docker-compose must tear down stack with 'if: always()' and 'docker compose down -v --remove-orphans'"
        )


def _validate_planning_jobs(jobs: Mapping[str, _ParsedJobSteps], label: str) -> None:
    if "planning" not in jobs:
        raise CIValidationError(f"{label} must define required job 'planning'")
    planning = jobs["planning"]
    _require_action_prefix(
        planning.unconditional_uses, "actions/checkout@", f"{label} jobs.planning"
    )
    _require_action_prefix(
        planning.unconditional_uses, "actions/setup-python@", f"{label} jobs.planning"
    )

    def _runs_python_script(script_rel: str) -> Any:
        pattern = re.compile(
            r"^(?:uv\s+run\s+(?:--project\s+\S+\s+)?)?python3?\s+"
            + re.escape(script_rel)
            + r"(?:\s|$)"
        )
        return lambda c: pattern.search(c) is not None

    planning_gates: tuple[tuple[str, Any], ...] = (
        (
            "python scripts/progress.py --write",
            _runs_python_script("scripts/progress.py --write"),
        ),
        (
            "git diff --exit-code -- docs/PROGRESS.md docs/REQUIREMENTS_TRACEABILITY.md",
            lambda c: (
                "git diff --exit-code" in c
                and "docs/PROGRESS.md" in c
                and "docs/REQUIREMENTS_TRACEABILITY.md" in c
            ),
        ),
        (
            "python scripts/verify_locks.py",
            _runs_python_script("scripts/verify_locks.py"),
        ),
        (
            "python scripts/verify_compose.py",
            _runs_python_script("scripts/verify_compose.py"),
        ),
        (
            "python scripts/verify_ci.py",
            _runs_python_script("scripts/verify_ci.py"),
        ),
        (
            "ruff check on scripts",
            lambda c: "ruff check" in c and "scripts" in c,
        ),
        (
            "ruff format --check on scripts",
            lambda c: "ruff format --check" in c and "scripts" in c,
        ),
        (
            "mypy --strict on scripts",
            lambda c: "mypy --strict" in c and "scripts" in c,
        ),
        (
            "unittest discover -s scripts/tests",
            lambda c: "unittest discover" in c and "scripts/tests" in c,
        ),
    )
    for desc, pred in planning_gates:
        _require_run_predicate(
            planning.unconditional_runs, pred, desc, f"{label} jobs.planning"
        )


def validate_workflow_file(path: Path, root: Path) -> tuple[str, int]:
    label = f".github/workflows/{path.name}"
    _require_regular_file(path, root, label)
    workflow = parse_workflow_yaml(_read_utf8(path, label), label)

    unknown_top = set(workflow.keys()) - ALLOWED_WORKFLOW_TOP_KEYS
    if unknown_top:
        raise CIValidationError(
            f"{label} contains unsupported top-level keys: {sorted(unknown_top)}"
        )

    wf_name = workflow.get("name")
    if not isinstance(wf_name, str) or not wf_name.strip():
        raise CIValidationError(f"{label} must declare a non-empty 'name'")

    _validate_triggers(workflow.get("on"), label)
    _validate_permissions(
        workflow.get("permissions"), f"{label} permissions", require_exact=True
    )
    _validate_concurrency(workflow.get("concurrency"), label)

    raw_jobs = workflow.get("jobs")
    if not isinstance(raw_jobs, dict) or not raw_jobs:
        raise CIValidationError(f"{label} must declare a non-empty 'jobs' mapping")

    parsed_jobs: dict[str, _ParsedJobSteps] = {}
    for job_id, job_cfg in raw_jobs.items():
        if not isinstance(job_id, str) or not job_id.strip():
            raise CIValidationError(f"{label} contains an invalid job identifier")
        parsed_jobs[job_id] = _validate_and_collect_job(job_id, job_cfg, label)

    if path.name == "product-preview.yml":
        _validate_product_preview_jobs(parsed_jobs, label)
    elif path.name == "planning.yml":
        _validate_planning_jobs(parsed_jobs, label)

    return path.name, len(parsed_jobs)


def validate_repository_ci(root: Path = ROOT) -> CISummary:
    if not isinstance(root, Path):
        raise CIValidationError("Repository root must be a pathlib.Path")
    if root.is_symlink():
        raise CIValidationError(f"Repository root must not be a symlink: {root}")
    if not root.exists() or not root.is_dir():
        raise CIValidationError(f"Repository root directory does not exist: {root}")

    _require_safe_dir(root / ".github", root, ".github")
    workflows_dir = _require_safe_dir(
        root / ".github" / "workflows", root, ".github/workflows"
    )

    entries = sorted(workflows_dir.iterdir(), key=lambda p: p.name)
    if not entries:
        raise CIValidationError("No workflow files found in .github/workflows")

    workflow_files: list[Path] = []
    for entry in entries:
        label = f".github/workflows/{entry.name}"
        if entry.is_symlink():
            raise CIValidationError(f"{label} must not be a symlink: {entry}")
        if not entry.is_file():
            raise CIValidationError(
                f"Unexpected non-file entry in .github/workflows: {entry.name}"
            )
        if entry.suffix not in (".yml", ".yaml"):
            raise CIValidationError(
                f"Unexpected non-YAML file in .github/workflows: {entry.name}"
            )
        workflow_files.append(entry)

    discovered_names = {wf.name for wf in workflow_files}
    for req_name in REQUIRED_WORKFLOW_FILES:
        if req_name not in discovered_names:
            raise CIValidationError(
                f"Missing required workflow file .github/workflows/{req_name}"
            )

    validated_names: list[str] = []
    total_jobs = 0
    for wf_path in workflow_files:
        wf_name, job_count = validate_workflow_file(wf_path, root)
        validated_names.append(wf_name)
        total_jobs += job_count

    return CISummary(workflows=tuple(validated_names), jobs=total_jobs)


def _validate_gate_spec(spec: GateCommandSpec) -> None:
    if not isinstance(spec, GateCommandSpec):
        raise CIValidationError("Each gate entry must be a GateCommandSpec instance")
    if not isinstance(spec.name, str) or not spec.name.strip():
        raise CIValidationError("GateCommandSpec.name must be a non-empty string")
    if not isinstance(spec.argv, tuple) or not spec.argv:
        raise CIValidationError(
            f"GateCommandSpec {spec.name!r} argv must be a non-empty tuple of strings"
        )
    for token in spec.argv:
        if not isinstance(token, str) or not token.strip():
            raise CIValidationError(
                f"GateCommandSpec {spec.name!r} argv tokens must be non-empty strings"
            )
    _check_no_exit_code_masking(" ".join(spec.argv), f"GateCommandSpec {spec.name!r}")
    if not isinstance(spec.cwd, Path):
        raise CIValidationError(
            f"GateCommandSpec {spec.name!r} cwd must be a pathlib.Path"
        )
    if spec.cwd.is_symlink() or not spec.cwd.exists() or not spec.cwd.is_dir():
        raise CIValidationError(
            f"GateCommandSpec {spec.name!r} cwd must be an existing non-symlink directory: {spec.cwd}"
        )
    if (
        isinstance(spec.timeout_seconds, bool)
        or not isinstance(spec.timeout_seconds, (int, float))
        or not math.isfinite(spec.timeout_seconds)
        or spec.timeout_seconds <= 0
        or spec.timeout_seconds > MAX_COMMAND_TIMEOUT_SECONDS
    ):
        raise CIValidationError(
            f"GateCommandSpec {spec.name!r} timeout_seconds must be in (0, {MAX_COMMAND_TIMEOUT_SECONDS}]"
        )


def run_gate_commands(
    commands: Sequence[GateCommandSpec],
    *,
    fail_fast: bool = True,
) -> GateRunReport:
    if not commands:
        raise CIValidationError("Gate command sequence must not be empty")
    for spec in commands:
        _validate_gate_spec(spec)

    results: list[GateStepResult] = []
    first_failure_code = 0

    for spec in commands:
        merged_env: dict[str, str] | None = None
        if spec.env is not None:
            merged_env = os.environ.copy()
            for key, val in spec.env.items():
                if not isinstance(key, str) or not key or not isinstance(val, str):
                    raise CIValidationError(
                        f"GateCommandSpec {spec.name!r} env entries must map non-empty str to str"
                    )
                merged_env[key] = val

        started = time.monotonic()
        try:
            proc = subprocess.run(
                list(spec.argv),
                cwd=spec.cwd,
                env=merged_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=spec.timeout_seconds,
                check=False,
                shell=False,
            )
            returncode = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            raw_out = exc.stdout
            raw_err = exc.stderr
            stdout = (
                raw_out.decode("utf-8", errors="replace")
                if isinstance(raw_out, bytes)
                else (raw_out or "")
            )
            err_prefix = (
                raw_err.decode("utf-8", errors="replace")
                if isinstance(raw_err, bytes)
                else (raw_err or "")
            )
            stderr = (
                f"{err_prefix}\nGate {spec.name!r} timed out after {spec.timeout_seconds}s"
            ).strip()
        except OSError as exc:
            returncode = 127
            stdout = ""
            stderr = f"Gate {spec.name!r} failed to execute: {exc}"

        elapsed = time.monotonic() - started
        results.append(
            GateStepResult(
                name=spec.name,
                argv=spec.argv,
                cwd=spec.cwd,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=elapsed,
            )
        )
        if returncode != 0:
            if first_failure_code == 0:
                first_failure_code = returncode
            if fail_fast:
                break

    passed = first_failure_code == 0
    return GateRunReport(
        passed=passed,
        exit_code=0 if passed else first_failure_code,
        results=tuple(results),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate GitHub Actions CI quality gate workflows and contracts."
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="Repository root path containing .github/workflows/.",
    )
    args = parser.parse_args(argv)
    if not isinstance(args.root, str) or not args.root.strip():
        print(
            "CI validation failed: --root must be a non-empty path",
            file=sys.stderr,
        )
        return 1
    try:
        summary = validate_repository_ci(Path(args.root))
    except CIValidationError as exc:
        print(f"CI validation failed: {exc}", file=sys.stderr)
        return 1
    workflows_csv = ",".join(summary.workflows)
    print(f"CI workflows verified: workflows={workflows_csv}, jobs={summary.jobs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
