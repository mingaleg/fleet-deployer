import pytest
from fastapi.testclient import TestClient

from fleet_deployer.app import create_app

from .conftest import SHA2

AUTH = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def client(fleet, runner):
    runner.toplevels = {(SHA2, "vps"): "/s/v2", (SHA2, "ronove"): "/s/r2"}
    fleet.refresh_main()
    with TestClient(create_app(fleet, start_background=False)) as c:
        yield c


def test_reads_are_open(client):
    assert client.get("/api/hosts").status_code == 200
    assert client.get("/api/main").json()["sha"] == SHA2
    assert client.get("/api/hosts/vps").json()["expected_path"] == "/s/v2"
    assert client.get("/api/hosts/nope").status_code == 404


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "s3cret"}])
def test_actions_need_the_token(client, headers):
    assert client.post("/api/deploy", json={"host": "vps", "mode": "test"}, headers=headers).status_code == 401
    assert client.post("/api/refresh", headers=headers).status_code == 401


def test_deploy_queues_a_job(client, fleet):
    r = client.post("/api/deploy", json={"host": "vps", "mode": "test"}, headers=AUTH)
    assert r.status_code == 202
    job = client.get(f"/api/jobs/{r.json()['job']}").json()
    assert job["state"] == "queued" and job["sha"] == SHA2


def test_deploy_errors(client):
    assert client.post("/api/deploy", json={"host": "mingamini", "mode": "test"}, headers=AUTH).status_code == 409
    assert client.post("/api/deploy", json={"host": "nope", "mode": "test"}, headers=AUTH).status_code == 404
    assert client.post("/api/deploy", json={"host": "vps", "mode": "dance"}, headers=AUTH).status_code == 422


def test_no_token_configured_disables_actions(client, fleet):
    fleet.config.token_file.write_text("")
    assert client.post("/api/refresh", headers=AUTH).status_code == 503
