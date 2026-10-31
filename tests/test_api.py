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


@pytest.fixture
def lan_client(fleet, runner):
    runner.toplevels = {(SHA2, "vps"): "/s/v2"}
    fleet.refresh_main()
    fleet.config.trusted_networks.extend(["172.26.249.0/24", "10.100.0.0/24"])
    app = create_app(fleet, start_background=False)
    with TestClient(app, client=("172.26.249.2", 50000)) as lan, TestClient(app, client=("8.8.8.8", 50000)) as outside:
        yield lan, outside


def test_trusted_network_acts_without_token(lan_client):
    lan, outside = lan_client
    assert lan.get("/api/session").json() == {"can_act": True, "via": "network"}
    assert lan.post("/api/deploy", json={"host": "vps", "mode": "test"}).status_code == 202
    assert outside.get("/api/session").json() == {"can_act": False, "via": None}
    assert outside.post("/api/deploy", json={"host": "vps", "mode": "test"}).status_code == 401
    assert outside.get("/api/session", headers=AUTH).json() == {"can_act": True, "via": "token"}


def test_is_trusted(config):
    config.trusted_networks.extend(["172.26.249.0/24", "10.100.0.0/24", "127.0.0.0/8"])
    assert config.is_trusted("10.100.0.33")
    assert config.is_trusted("::ffff:172.26.249.9")
    assert config.is_trusted("127.0.0.1")
    assert not config.is_trusted("10.200.0.2")
    assert not config.is_trusted("testclient")
    assert not config.is_trusted(None)


def test_responses_are_revalidated(client):
    assert client.get("/api/hosts").headers["cache-control"] == "no-cache"
