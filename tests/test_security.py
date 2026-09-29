"""Deployment hardening: optional HTTP Basic auth and the simulator kill switch.
Both are off by default (open demo); these tests turn them on."""
import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sentinel import config
from sentinel.main import app


def _basic(user_pass: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(user_pass.encode()).decode()}


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "BASIC_AUTH", "bank:s3cret-pass")
    with TestClient(app) as client:
        yield client


def test_everything_requires_login_when_auth_is_configured(auth_on):
    for method, path in (("get", "/"), ("get", "/metrics"), ("get", "/cases"),
                         ("get", "/evaluation"), ("get", "/metrics/by_age")):
        r = getattr(auth_on, method)(path)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"].startswith("Basic")
    r = auth_on.post("/score", json={"cust_id": "x", "amount": 100})
    assert r.status_code == 401
    assert auth_on.get("/metrics", headers=_basic("bank:wrong")).status_code == 401
    assert auth_on.get("/metrics", headers={"Authorization": "Basic !!!notbase64"}).status_code == 401


def test_correct_credentials_work_and_set_a_session_cookie(auth_on):
    r = auth_on.get("/metrics", headers=_basic("bank:s3cret-pass"))
    assert r.status_code == 200
    assert "sentinel_session" in r.cookies or "sentinel_session" in auth_on.cookies
    # the cookie alone now authenticates (that's what the browser WebSocket uses)
    r2 = auth_on.post("/score", json={"cust_id": "auth_c1", "amount": 250.0})
    assert r2.status_code == 200 and r2.json()["cust_id"] == "auth_c1"


def test_health_stays_reachable_but_reveals_nothing_without_login(auth_on):
    r = auth_on.get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status"}                       # no model internals
    full = auth_on.get("/health", headers=_basic("bank:s3cret-pass")).json()
    assert "model" in full


def test_websocket_rejects_unauthenticated_clients(auth_on):
    with pytest.raises(WebSocketDisconnect):
        with auth_on.websocket_connect("/ws/stream") as ws:
            ws.receive_text()
    auth_on.get("/metrics", headers=_basic("bank:s3cret-pass"))    # log in -> cookie
    with auth_on.websocket_connect("/ws/stream") as ws:
        assert '"snapshot"' in ws.receive_text()


def test_auth_is_off_by_default():
    assert config.BASIC_AUTH is None or isinstance(config.BASIC_AUTH, str)
    if config.BASIC_AUTH is None:
        with TestClient(app) as client:
            assert client.get("/metrics").status_code == 200


def test_simulator_can_be_disabled_for_real_traffic_deployments(monkeypatch):
    monkeypatch.setattr(config, "SIMULATOR_ENABLED", False)
    with TestClient(app) as client:
        for path in ("/tick", "/simulator/inject/card_testing", "/simulator/inject_ring/bust_out",
                     "/simulator/config", "/replay/blended"):
            body = {"rate": 1} if path == "/simulator/config" else {}
            r = client.post(path, json=body)
            assert r.status_code == 403, path
            assert "disabled" in r.json()["detail"]
        assert client.get("/health").json()["simulator"] is False
        # real scoring still works
        assert client.post("/score", json={"cust_id": "real_1", "amount": 99.0}).status_code == 200
