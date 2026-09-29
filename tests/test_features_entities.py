from datetime import datetime, timedelta

from sentinel.entities import ENTITY_FEATURES, EntityRegistry
from sentinel.features import FEATURE_COLUMNS, ProfileState, compute_features


def _txn(**kw):
    base = dict(cust_id="C1", ts=datetime(2025, 6, 1, 12), amount=50.0, mcc="grocery",
               channel="pos", merchant_id="m1", beneficiary="", country="US",
               city="NYC", lat=40.71, lon=-74.01, device_id="dev-a", card_bin="440000",
               label=0)
    base.update(kw)
    return base


def test_feature_row_has_every_column():
    ps = ProfileState("C1", "US", 40.71, -74.01, datetime(2024, 1, 1))
    feat = compute_features(_txn(), ps, EntityRegistry(), datetime(2025, 6, 1, 12))
    assert set(feat) == set(FEATURE_COLUMNS)
    assert all(k in feat for k in ENTITY_FEATURES)


def test_impossible_travel_and_new_entity_flags():
    ps = ProfileState("C1", "US", 40.71, -74.01, datetime(2024, 1, 1))
    ps.update(_txn(ts=datetime(2025, 6, 1, 12)))
    far = _txn(ts=datetime(2025, 6, 1, 12, 30), country="SG", lat=1.35, lon=103.82,
               merchant_id="m2", device_id="dev-b")
    feat = compute_features(far, ps, EntityRegistry(), far["ts"])
    assert feat["impossible_travel"] == 1.0
    assert feat["new_country"] == 1.0 and feat["new_device"] == 1.0


def test_entity_fraud_rate_moves_toward_evidence():
    reg = EntityRegistry()
    now = datetime(2025, 6, 1, 12)
    base = reg.snapshot_features(_txn(merchant_id="shady"), now)["merchant_fraud_rate"]
    for i in range(40):
        reg.observe(_txn(merchant_id="shady", cust_id=f"C{i}"), 1, now + timedelta(minutes=i))
    after = reg.snapshot_features(_txn(merchant_id="shady"), now + timedelta(hours=1))["merchant_fraud_rate"]
    assert after > base + 0.3            # rose well above the prior
    assert after < 1.0                   # but stays smoothed


def test_graph_fanin_detects_shared_mule():
    reg = EntityRegistry()
    now = datetime(2025, 6, 1, 12)
    for i in range(6):
        reg.observe(_txn(cust_id=f"V{i}", channel="transfer", beneficiary="mule-1"),
                    1, now + timedelta(minutes=i))
    f = reg.snapshot_features(_txn(cust_id="V7", channel="transfer", beneficiary="mule-1"), now)
    assert f["beneficiary_customer_fanin"] == 6
    assert f["ring_size"] == 6


def test_confirm_fraud_bumps_rate_without_new_txn():
    reg = EntityRegistry()
    now = datetime(2025, 6, 1, 12)
    for i in range(10):
        reg.observe(_txn(device_id="d9", cust_id=f"C{i}"), 0, now)
    before = reg.snapshot_features(_txn(device_id="d9"), now)["device_fraud_rate"]
    for _ in range(5):
        reg.confirm_fraud(_txn(device_id="d9"), now)
    after = reg.snapshot_features(_txn(device_id="d9"), now)["device_fraud_rate"]
    assert after > before
