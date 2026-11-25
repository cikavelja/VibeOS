import pytest
from fastapi.testclient import TestClient
from app.main import create_app

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{tmp_path / 'test.db'}")) as c:
        yield c

def user(client, name):
    data = {"username": name, "password": "test-password-123"}
    assert client.post("/api/v1/auth/register", json=data).status_code == 201
    token = client.post("/api/v1/auth/login", json=data).json()["token"]
    return {"Authorization": f"Bearer {token}"}

def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/api/v1/projects").status_code == 401

def test_share_isolates_data(client):
    a, b = user(client,"alice"), user(client,"bob")
    definition = {"name":"Notes", "components":[{"id":"note","type":"input","label":"Note"}]}
    p = client.post("/api/v1/projects",json=definition,headers=a).json()["id"]
    i = client.post(f"/api/v1/projects/{p}/install",headers=a).json()["id"]
    assert client.put(f"/api/v1/installations/{i}/state",json={"values":{"note":"private"}},headers=a).status_code == 200
    assert client.post(f"/api/v1/projects/{p}/install",headers=b).status_code == 404
    s = client.post(f"/api/v1/projects/{p}/share",json={"recipient":"bob"},headers=a).json()["id"]
    assert client.post(f"/api/v1/shares/{s}/accept",headers=a).status_code == 404
    j = client.post(f"/api/v1/shares/{s}/accept",headers=b).json()["id"]
    assert client.post(f"/api/v1/shares/{s}/accept",headers=b).json()["id"] == j
    assert client.get("/api/v1/installations",headers=b).json() == [{"id":j,"definition":definition,"state":{}}]
    assert client.put(f"/api/v1/installations/{i}/state",json={"values":{"note":"stolen"}},headers=b).status_code == 404
    assert client.put(f"/api/v1/installations/{j}/state",json={"values":{"note":"bob"}},headers=b).status_code == 200
    assert client.get("/api/v1/installations",headers=a).json()[0]["state"] == {"note":"private"}

def test_validation_and_logout(client):
    a = user(client,"alice")
    assert client.post("/api/v1/projects",headers=a,json={"name":"bad","components":[{"id":"x","type":"script","label":"bad"}]}).status_code == 422
    assert client.post("/api/v1/auth/logout",headers=a).status_code == 200
    assert client.get("/api/v1/projects",headers=a).status_code == 401
