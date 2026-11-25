from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core.config import resolve_database_url
from app.infrastructure.database import make_database
from app.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _allocate_loopback_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            addr = sock.getsockname()
            ports.append(int(addr[1]))
            sockets.append(sock)
    finally:
        for sock in sockets:
            sock.close()
    return ports


def _run_compose(
    docker_bin: str, args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [docker_bin, "compose", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _parse_compose_ps_json(raw: str) -> list[dict[str, Any]]:
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        loaded = json.loads(stripped)
        assert isinstance(loaded, list)
        return [item for item in loaded if isinstance(item, dict)]
    rows: list[dict[str, Any]] = []
    for line in stripped.splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def test_compose_contract_passes() -> None:
    spec = importlib.util.spec_from_file_location(
        "verify_compose", REPO_ROOT / "scripts" / "verify_compose.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_compose"] = module
    spec.loader.exec_module(module)
    summary = module.validate_repository_compose(REPO_ROOT)
    assert summary.services == ("postgres", "backend", "frontend")
    assert summary.dockerfiles == 2


def test_postgresql_driver_configuration_and_secret_redaction_on_failure() -> None:
    secret_url_pw = "UNREACHABLE_SECRET_PW_9812"
    engine, _ = make_database(f"postgresql://vibeos:{secret_url_pw}@127.0.0.1:1/vibeos")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert getattr(engine.pool, "_pre_ping", False) is True
    finally:
        engine.dispose()

    secret_app_pw = "UNREACHABLE_APP_SECRET_4410"
    api = create_app(f"postgresql+psycopg://vibeos:{secret_app_pw}@127.0.0.1:1/vibeos")
    with TestClient(api, raise_server_exceptions=False) as client:
        health_res = client.get("/api/v1/health")
        assert health_res.status_code == 503
        assert health_res.json() == {"detail": "Database unavailable"}
        assert secret_app_pw not in health_res.text

        reg_res = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "test-password-123"},
        )
        assert reg_res.status_code == 503
        assert reg_res.json() == {"detail": "Database unavailable"}
        assert secret_app_pw not in reg_res.text

    malformed_secret = "MALFORMED_URL_SECRET_7721"
    for bad_url in (
        f"postgresql+psycopg://vibeos:{malformed_secret}@localhost:notaport/vibeos",
        f"postgresql+psycopg://vibeos:{malformed_secret}@/vibeos",
    ):
        with pytest.raises(ValueError) as exc_info:
            resolve_database_url(bad_url)
        assert malformed_secret not in str(exc_info.value)
        assert malformed_secret not in repr(exc_info.value)
        assert exc_info.value.__cause__ is None


def test_docker_compose_stack_reaches_healthy_and_persists_to_postgresql() -> None:
    docker_bin = shutil.which("docker")
    assert docker_bin is not None, (
        "docker CLI is required for Compose integration tests"
    )

    pg_port, backend_port, frontend_port = _allocate_loopback_ports(3)
    project_name = f"vibeos-it-{uuid4().hex[:8]}"
    env = os.environ.copy()
    env["POSTGRES_PORT"] = str(pg_port)
    env["BACKEND_PORT"] = str(backend_port)
    env["FRONTEND_PORT"] = str(frontend_port)
    env.pop("DATABASE_URL", None)
    env.pop("API_TARGET", None)

    try:
        up_proc = _run_compose(
            docker_bin,
            [
                "-p",
                project_name,
                "up",
                "-d",
                "--build",
                "--wait",
                "--wait-timeout",
                "180",
            ],
            env,
        )
        assert up_proc.returncode == 0, (
            f"docker compose up failed:\n{up_proc.stdout}\n{up_proc.stderr}"
        )

        ps_proc = _run_compose(
            docker_bin,
            ["-p", project_name, "ps", "--format", "json"],
            env,
        )
        assert ps_proc.returncode == 0, ps_proc.stderr
        containers = _parse_compose_ps_json(ps_proc.stdout)
        by_service = {str(item.get("Service")): item for item in containers}
        assert set(by_service.keys()) == {"postgres", "backend", "frontend"}
        for svc_name, info in by_service.items():
            assert info.get("State") == "running", (
                f"{svc_name} state={info.get('State')}"
            )
            assert info.get("Health") == "healthy", (
                f"{svc_name} health={info.get('Health')}"
            )
            publishers = info.get("Publishers")
            if isinstance(publishers, list):
                for pub in publishers:
                    if isinstance(pub, dict) and pub.get("PublishedPort"):
                        assert pub.get("URL") == "127.0.0.1", (
                            f"{svc_name} published on non-loopback {pub!r}"
                        )

        expected_health = {"status": "ok", "service": "vibeos", "version": "0.1.0"}
        with httpx.Client(timeout=10.0) as http:
            be_health = http.get(f"http://127.0.0.1:{backend_port}/api/v1/health")
            assert be_health.status_code == 200
            assert be_health.json() == expected_health

            fe_root = http.get(f"http://127.0.0.1:{frontend_port}/")
            assert fe_root.status_code == 200
            assert '<div id="root">' in fe_root.text

            fe_health = http.get(f"http://127.0.0.1:{frontend_port}/api/v1/health")
            assert fe_health.status_code == 200
            assert fe_health.json() == expected_health

            bad_query = http.get(
                f"http://127.0.0.1:{frontend_port}/api/v1/health?unexpected=1"
            )
            assert bad_query.status_code == 422
            assert bad_query.json() == {"detail": "Unexpected query parameters"}

            base = f"http://127.0.0.1:{frontend_port}"
            alice_creds = {"username": "alice", "password": "test-password-123"}
            bob_creds = {"username": "bob", "password": "test-password-123"}
            assert (
                http.post(f"{base}/api/v1/auth/register", json=alice_creds).status_code
                == 201
            )
            assert (
                http.post(f"{base}/api/v1/auth/register", json=alice_creds).status_code
                == 409
            )
            assert (
                http.post(f"{base}/api/v1/auth/register", json=bob_creds).status_code
                == 201
            )

            alice_token = http.post(
                f"{base}/api/v1/auth/login", json=alice_creds
            ).json()["token"]
            bob_token = http.post(f"{base}/api/v1/auth/login", json=bob_creds).json()[
                "token"
            ]
            alice_headers = {"Authorization": f"Bearer {alice_token}"}
            bob_headers = {"Authorization": f"Bearer {bob_token}"}

            definition = {
                "name": "PostgresCounter",
                "components": [{"id": "count", "type": "counter", "label": "Count"}],
            }
            proj_res = http.post(
                f"{base}/api/v1/projects", json=definition, headers=alice_headers
            )
            assert proj_res.status_code == 201
            proj_id = proj_res.json()["id"]

            assert (
                http.post(
                    f"{base}/api/v1/projects/{proj_id}/install", headers=bob_headers
                ).status_code
                == 404
            )
            inst_id = http.post(
                f"{base}/api/v1/projects/{proj_id}/install", headers=alice_headers
            ).json()["id"]
            assert (
                http.put(
                    f"{base}/api/v1/installations/{inst_id}/state",
                    json={"values": {"count": 7}},
                    headers=alice_headers,
                ).status_code
                == 200
            )
            share_id = http.post(
                f"{base}/api/v1/projects/{proj_id}/share",
                json={"recipient": "bob"},
                headers=alice_headers,
            ).json()["id"]
            bob_inst_id = http.post(
                f"{base}/api/v1/shares/{share_id}/accept", headers=bob_headers
            ).json()["id"]
            assert http.get(
                f"{base}/api/v1/installations", headers=bob_headers
            ).json() == [{"id": bob_inst_id, "definition": definition, "state": {}}]
    finally:
        down_proc = _run_compose(
            docker_bin,
            ["-p", project_name, "down", "-v", "--remove-orphans"],
            env,
        )
        assert down_proc.returncode == 0, down_proc.stderr


def test_docker_compose_fails_closed_on_bad_postgres_credentials_without_secret_leak() -> (
    None
):
    docker_bin = shutil.which("docker")
    assert docker_bin is not None, (
        "docker CLI is required for Compose integration tests"
    )

    pg_port, backend_port, frontend_port = _allocate_loopback_ports(3)
    project_name = f"vibeos-bad-{uuid4().hex[:8]}"
    server_secret = "pg_server_secret_alpha_9182"
    wrong_secret = "pg_wrong_secret_bravo_7364"

    env = os.environ.copy()
    env["POSTGRES_PORT"] = str(pg_port)
    env["BACKEND_PORT"] = str(backend_port)
    env["FRONTEND_PORT"] = str(frontend_port)
    env["POSTGRES_PASSWORD"] = server_secret
    env["DATABASE_URL"] = (
        f"postgresql+psycopg://vibeos:{wrong_secret}@postgres:5432/vibeos"
    )

    try:
        up_proc = _run_compose(
            docker_bin,
            [
                "-p",
                project_name,
                "up",
                "-d",
                "--build",
                "--wait",
                "--wait-timeout",
                "35",
            ],
            env,
        )
        assert up_proc.returncode != 0
        combined_cli = f"{up_proc.stdout}\n{up_proc.stderr}"
        assert server_secret not in combined_cli
        assert wrong_secret not in combined_cli

        with httpx.Client(timeout=5.0) as http:
            res = http.get(f"http://127.0.0.1:{backend_port}/api/v1/health")
            assert res.status_code == 503
            assert res.json() == {"detail": "Database unavailable"}
            assert server_secret not in res.text
            assert wrong_secret not in res.text

        logs_proc = _run_compose(
            docker_bin,
            ["-p", project_name, "logs", "backend"],
            env,
        )
        combined_logs = f"{logs_proc.stdout}\n{logs_proc.stderr}"
        assert server_secret not in combined_logs
        assert wrong_secret not in combined_logs
    finally:
        _run_compose(
            docker_bin,
            ["-p", project_name, "down", "-v", "--remove-orphans"],
            env,
        )
