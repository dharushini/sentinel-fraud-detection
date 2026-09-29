from datetime import datetime

from fastapi.testclient import TestClient

from sentinel.main import app


def test_api_surface():
    with TestClient(app) as client:                 # startup: load model + warm state
        h = client.get("/health").json()
        assert h["status"] == "ok"
        assert h["model"]["roc_auc"] > 0.9
        assert h["drift"]["state"] in ("warming", "stable", "watch", "alert")
        assert h["state_backend"] in ("disk", "redis")

        r = client.post("/score", json={
            "cust_id": "C00001", "amount": 25.0, "mcc": "grocery", "channel": "pos",
            "merchant_id": "grocery_C00001_0", "country": "US"}).json()
        assert r["action"] in ("ALLOW", "REVIEW", "CHALLENGE", "BLOCK")
        assert "explanation" in r and "features" in r

        fb = client.post("/feedback", json={
            "cust_id": "C00001", "ts": datetime(2025, 6, 1, 12).isoformat(),
            "amount": 25.0, "label": 1, "kind": "chargeback"}).json()
        assert fb["total"] >= 1

        sc = client.get("/scenarios").json()
        assert "geo_consistent_ato" in sc["adversarial"]
        assert client.post("/simulator/inject/slow_drip").json()["events"] > 0
        assert "psi" in client.get("/drift").json()
        assert client.get("/metrics").json()["processed"] >= 1
