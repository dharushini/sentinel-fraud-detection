"""Regression tests for the STATE["sim"] cold-start crash.

/simulator/inject, /simulator/inject_ring and /simulator/config used to read
STATE["sim"] directly. On a genuinely cold process — no lifespan event has
run yet, which is exactly what happens on a serverless cold start, or any
request that reaches one of these routes before the FastAPI startup event
has completed — STATE["sim"] doesn't exist yet and raises an unhandled
KeyError, which FastAPI turns into a 500 with no useful body. The dashboard
then read fields like `cust_id`/`victim_city` off that error body and
rendered "undefined" in the toast/status text.

The fix: `_sim()`, mirroring the existing `_engine()` lazy-init helper, so
any route can be the very first thing that runs against a fresh process.

IMPORTANT: TestClient(app) used WITHOUT the `with` context manager does NOT
run the startup lifespan event — that's exactly the "genuinely cold" state
these tests need to reproduce the bug. (Every other test file in this suite
uses `with TestClient(app) as client:`, which runs startup first and would
never have caught this.)
"""
from fastapi.testclient import TestClient

from sentinel.main import app


def test_inject_works_on_a_cold_process_with_no_prior_requests():
    client = TestClient(app)                    # deliberately no `with` / lifespan
    r = client.post("/simulator/inject/card_testing")
    assert r.status_code == 200
    j = r.json()
    assert j.get("cust_id") is not None
    assert j.get("victim_city") is not None
    assert j.get("events", 0) > 0


def test_inject_ring_works_on_a_cold_process():
    client = TestClient(app)
    r = client.post("/simulator/inject_ring/bust_out?ring_size=3")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ring_device") is not None
    assert j.get("ring_beneficiary") is not None
    assert j.get("n_cases", 0) >= 0


def test_simulator_config_works_on_a_cold_process():
    client = TestClient(app)
    r = client.post("/simulator/config", json={"rate": 5, "fraud_rate": 0.02})
    assert r.status_code == 200
    j = r.json()
    assert j["rate"] == 5
    assert j["fraud_rate"] == 0.02


def test_tick_works_on_a_cold_process():
    client = TestClient(app)
    r = client.post("/tick", params={"n": 5})
    assert r.status_code == 200
    j = r.json()
    assert "cases" in j and "metrics" in j
