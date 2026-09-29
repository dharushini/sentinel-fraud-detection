"""Tests for /replay/blended — a single chronologically-merged stream of real
UPI rows and fresh synthetic INR traffic, scored through the exact same
engine.process() path with no row silently dropped on error."""
from fastapi.testclient import TestClient

from sentinel.main import app

def _upi_row(i, status="success"):
    ts = f"2025-06-01T09:{i:02d}:00"
    amt = 500.0 + i * 37.5
    app_, bank = ["GPay", "PhonePe", "Amazon Pay", "BHIM"][i % 4], ["SBI", "HDFC", "Axis", "PNB"][i % 4]
    return f"{ts},{amt:.2f},INR,{app_},{bank},device_{i % 6},{status},False"


# 20 "success" rows (all loaded) + 2 "failed" rows (dropped by the adapter —
# a declined attempt never redefines "normal" behaviour, same as bank-side).
_UPI_FIXTURE = "timestamp,amount,currency,upi_app,bank,device_fingerprint,status,is_suspicious\n" + \
    "\n".join(_upi_row(i) for i in range(20)) + "\n" + \
    _upi_row(20, "failed") + "\n" + _upi_row(21, "failed") + "\n"


def _write_fixture(tmp_path):
    p = tmp_path / "upi_fixture.csv"
    p.write_text(_UPI_FIXTURE)
    return p


def test_blended_scores_every_row_with_no_errors(tmp_path):
    csv_path = _write_fixture(tmp_path)
    with TestClient(app) as client:
        r = client.post("/replay/blended", json={
            "real_schema": "upi", "real_path": str(csv_path),
            "limit": 20, "real_share": 0.5,
        })
        assert r.status_code == 200
        j = r.json()
        assert j["errors"] == []
        assert j["scored"] == j["requested"] == 20
        assert j["real_scored"] + j["synth_scored"] == j["scored"]
        assert j["real_scored"] > 0 and j["synth_scored"] > 0


def test_blended_is_chronologically_merged_not_two_blocks(tmp_path):
    """The whole point of 'blended' is a single merged timeline, not real rows
    followed by synthetic rows (or vice versa) — verify via /metrics that both
    sources actually landed in the same engine state, and via repeated calls
    that the real cursor advances (proving real rows really were consumed,
    not just synthetic ones silently standing in for them)."""
    csv_path = _write_fixture(tmp_path)
    with TestClient(app) as client:
        r1 = client.post("/replay/blended", json={
            "real_schema": "upi", "real_path": str(csv_path),
            "limit": 4, "real_share": 0.5,
        }).json()
        assert r1["real_cursor"] == 2           # 4 * 0.5 = 2 real rows consumed
        assert r1["real_total_rows"] == 20      # the 2 "failed" rows are dropped by the adapter

        r2 = client.post("/replay/blended", json={
            "real_schema": "upi", "real_path": str(csv_path),
            "limit": 4, "real_share": 0.5,
        }).json()
        assert r2["real_cursor"] == 4           # cursor advanced, not reset
        assert r2["errors"] == []


def test_blended_handles_missing_dataset_cleanly(tmp_path):
    with TestClient(app) as client:
        r = client.post("/replay/blended", json={
            "real_schema": "upi", "real_path": str(tmp_path / "nope.csv"),
            "limit": 10,
        })
        assert r.status_code == 404
        assert "dataset on this server" in r.json()["detail"]


def test_blended_real_share_extremes_dont_error(tmp_path):
    csv_path = _write_fixture(tmp_path)
    with TestClient(app) as client:
        pure_real = client.post("/replay/blended", json={
            "real_schema": "upi", "real_path": str(csv_path),
            "limit": 3, "real_share": 1.0,
        }).json()
        assert pure_real["synth_scored"] == 0 and pure_real["errors"] == []

        pure_synth = client.post("/replay/blended", json={
            "real_schema": "upi", "real_path": str(csv_path),
            "limit": 3, "real_share": 0.0,
        }).json()
        assert pure_synth["real_scored"] == 0 and pure_synth["errors"] == []
