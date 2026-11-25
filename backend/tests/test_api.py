from collections.abc import Iterator
from pathlib import Path

import anyio
from fastapi.testclient import TestClient
import httpx
import pytest

from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}")) as c:
        yield c


def user(client: TestClient, name: str) -> dict[str, str]:
    data = {"username": name, "password": "test-password-123"}
    assert client.post("/api/v1/auth/register", json=data).status_code == 201
    token: str = client.post("/api/v1/auth/login", json=data).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "vibeos", "version": "0.1.0"}
    assert client.get("/api/v1/projects").status_code == 401


def test_health_async_contract(tmp_path: Path) -> None:
    api = create_app(f"sqlite:///{tmp_path / 'async_test.db'}")

    async def run_check() -> httpx.Response:
        async with api.router.lifespan_context(api):
            transport = httpx.ASGITransport(app=api)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as ac:
                return await ac.get("/api/v1/health")

    response = anyio.run(run_check)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "vibeos", "version": "0.1.0"}


def test_health_rejects_invalid_inputs(client: TestClient) -> None:
    query_res = client.get("/api/v1/health?unexpected=1")
    assert query_res.status_code == 422
    assert query_res.json()["detail"] == "Unexpected query parameters"

    method_res = client.post("/api/v1/health", json={"status": "override"})
    assert method_res.status_code == 405

    with pytest.raises(ValueError, match="DATABASE_URL must not be empty"):
        create_app("   ")

    with pytest.raises(ValueError, match="Unsupported DATABASE_URL scheme"):
        create_app("ftp://user:secret@example.com/db")


def test_health_reports_503_when_database_fails_without_leaking_secrets(
    tmp_path: Path,
) -> None:
    api = create_app(f"sqlite:///{tmp_path / 'broken.db'}")
    with TestClient(api) as test_client:

        def broken_factory() -> None:
            raise RuntimeError(
                "sqlite://secret-token@internal-host/broken.db connection failed"
            )

        api.state.factory = broken_factory
        res = test_client.get("/api/v1/health")
        assert res.status_code == 503
        assert res.json() == {"detail": "Database unavailable"}
        assert "secret-token" not in res.text


def test_share_isolates_data(client: TestClient) -> None:
    a, b = user(client, "alice"), user(client, "bob")
    definition = {
        "name": "Notes",
        "components": [{"id": "note", "type": "input", "label": "Note"}],
    }
    p = client.post("/api/v1/projects", json=definition, headers=a).json()["id"]
    i = client.post(f"/api/v1/projects/{p}/install", headers=a).json()["id"]
    assert (
        client.put(
            f"/api/v1/installations/{i}/state",
            json={"values": {"note": "private"}},
            headers=a,
        ).status_code
        == 200
    )
    assert client.post(f"/api/v1/projects/{p}/install", headers=b).status_code == 404
    s = client.post(
        f"/api/v1/projects/{p}/share", json={"recipient": "bob"}, headers=a
    ).json()["id"]
    assert client.post(f"/api/v1/shares/{s}/accept", headers=a).status_code == 404
    j = client.post(f"/api/v1/shares/{s}/accept", headers=b).json()["id"]
    assert client.post(f"/api/v1/shares/{s}/accept", headers=b).json()["id"] == j
    assert client.get("/api/v1/installations", headers=b).json() == [
        {"id": j, "definition": definition, "state": {}}
    ]
    assert (
        client.put(
            f"/api/v1/installations/{i}/state",
            json={"values": {"note": "stolen"}},
            headers=b,
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/v1/installations/{j}/state",
            json={"values": {"note": "bob"}},
            headers=b,
        ).status_code
        == 200
    )
    assert client.get("/api/v1/installations", headers=a).json()[0]["state"] == {
        "note": "private"
    }


def test_validation_and_logout(client: TestClient) -> None:
    a = user(client, "alice")
    assert (
        client.post(
            "/api/v1/projects",
            headers=a,
            json={
                "name": "bad",
                "components": [{"id": "x", "type": "script", "label": "bad"}],
            },
        ).status_code
        == 422
    )
    assert client.post("/api/v1/auth/logout", headers=a).status_code == 200
    assert client.get("/api/v1/projects", headers=a).status_code == 401
