from datetime import datetime, timedelta

import numpy as np

from sentinel.drift import DriftMonitor, psi, score_histogram
from sentinel.entities import EntityRegistry
from sentinel.feedback import FeedbackStore
from sentinel.features import ProfileState
from sentinel.state import SnapshotStore


# ---- drift --------------------------------------------------------------
def test_psi_zero_for_same_distribution():
    # reference must be built with the monitor's own binning (score_histogram)
    ref = score_histogram(np.random.RandomState(0).beta(2, 20, 5000) * 0.4)
    same = list(np.random.RandomState(1).beta(2, 20, 3000) * 0.4)
    assert psi(ref, same) < 0.1


def test_psi_alerts_on_shift():
    ref = score_histogram(np.random.RandomState(0).beta(2, 20, 5000) * 0.4)
    shifted = list(np.random.RandomState(1).beta(8, 3, 2000) * 0.4)   # < re-baseline
    m = DriftMonitor(ref)
    for v in shifted:
        m.observe(v)
    assert not m._baselined
    assert m.status()["state"] == "alert"


def test_drift_rebaselines_on_live_traffic():
    from sentinel.config import DRIFT_BASELINE_AFTER
    ref = score_histogram(np.random.RandomState(0).beta(2, 20, 5000) * 0.4)
    m = DriftMonitor(ref)
    # a steady stream that differs from the seeded reference...
    for v in np.random.RandomState(2).beta(5, 12, DRIFT_BASELINE_AFTER + 1500) * 0.4:
        m.observe(float(v))
    # ...gets adopted as the new normal, so the monitor settles
    assert m._baselined
    assert m.status()["state"] in ("stable", "watch")


# ---- feedback ----------------------------------------------------------
def test_feedback_merge_and_weights(tmp_path):
    fb = FeedbackStore(tmp_path / "fb.jsonl")
    ts = datetime(2025, 6, 1, 12)
    fb.record(cust_id="C1", ts=ts, amount=100.0, label=1, kind="chargeback")
    events = [{"type": "txn", "cust_id": "C1", "ts": ts, "amount": 100.0, "label": 0}]
    assert fb.merge_labels(events) == 1 and events[0]["label"] == 1

    reloaded = FeedbackStore(tmp_path / "fb.jsonl")
    assert reloaded.label_for("C1", ts, 100.0) == 1

    txns = [
        {"ts": datetime(2025, 1, 1), "label": 1},     # old confirmed fraud -> heavy
        {"ts": datetime(2025, 1, 1), "label": 0},     # old legit -> ~1
        {"ts": datetime(2025, 6, 1), "label": 0},     # very recent -> down-weighted
    ]
    w = FeedbackStore.sample_weights(txns, now=datetime(2025, 6, 1))
    assert w[0] > w[1] > w[2]


# ---- state snapshot --------------------------------------------------
def test_snapshot_roundtrip(tmp_path):
    store = SnapshotStore(tmp_path / "s.snapshot", redis_url="")
    ps = ProfileState("C1", "US", 40.71, -74.01, datetime(2024, 1, 1))
    ps.update({"amount": 50.0, "merchant_id": "m", "country": "US", "device_id": "d",
               "beneficiary": "", "lat": 40.71, "lon": -74.01, "ts": datetime(2025, 6, 1)})
    reg = EntityRegistry()
    reg.observe({"cust_id": "C1", "merchant_id": "m", "card_bin": "4", "device_id": "d",
                 "beneficiary": ""}, 1, datetime(2025, 6, 1))

    store.save({"profiles": {"C1": ps}, "entities": reg, "seq": 7})
    back = store.load()
    assert back["seq"] == 7
    assert back["profiles"]["C1"].n == 1
    assert back["entities"]._t["merchant"]["m"].f == 1.0
    assert store.backend == "disk"
