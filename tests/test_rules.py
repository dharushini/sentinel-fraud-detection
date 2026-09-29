from datetime import datetime, timedelta

from sentinel.entities import EntityRegistry
from sentinel.features import ProfileState, compute_features
from sentinel.rules import evaluate_rules, worst_severity


def _ps():
    ps = ProfileState("C1", "US", 40.71, -74.01, datetime(2024, 1, 1))
    base = datetime(2025, 6, 1, 12)
    for i in range(40):
        ps.update({"amount": 40 + (i % 5) * 4, "merchant_id": f"grocery_{i%3}",
                   "country": "US", "device_id": "dev-a", "beneficiary": "",
                   "lat": 40.71, "lon": -74.01, "ts": base - timedelta(days=40 - i)})
    return ps, base


def _txn(ps_now, **kw):
    base = dict(cust_id="C1", ts=ps_now, amount=60.0, mcc="grocery", channel="pos",
               merchant_id="grocery_1", beneficiary="", country="US", city="NYC",
               lat=40.71, lon=-74.01, device_id="dev-a", card_bin="440000")
    base.update(kw)
    return base


def test_impossible_travel_blocks():
    ps, now = _ps()
    ps.update(_txn(now))
    txn = _txn(now + timedelta(minutes=30), country="SG", lat=1.35, lon=103.82,
               merchant_id="x", mcc="electronics")
    feat = compute_features(txn, ps, EntityRegistry(), txn["ts"])
    assert worst_severity(evaluate_rules(feat, txn)) == "block"


def test_card_testing_blocks():
    ps, now = _ps()
    hits = []
    for i in range(7):
        t = now + timedelta(seconds=20 * i)
        txn = _txn(t, amount=1.5, channel="online", merchant_id=f"probe_{i}",
                   device_id="dev-bot", mcc="retail")
        feat = compute_features(txn, ps, EntityRegistry(), t)
        hits = evaluate_rules(feat, txn)
        ps.update(txn)
    assert any(h.code == "card_testing" for h in hits)


def test_known_bad_entity_and_ring_rules():
    ps, now = _ps()
    reg = EntityRegistry()
    for i in range(30):
        reg.observe(_txn(now, merchant_id="badm", beneficiary="mule-1",
                         channel="transfer", cust_id=f"V{i}"), 1, now)
    txn = _txn(now + timedelta(hours=2), merchant_id="badm", beneficiary="mule-1",
               channel="transfer", amount=300)
    feat = compute_features(txn, ps, reg, txn["ts"])
    codes = {h.code for h in evaluate_rules(feat, txn)}
    assert "known_bad_entity" in codes
    assert "fraud_ring" in codes


def test_normal_txn_is_clean():
    ps, now = _ps()
    txn = _txn(now + timedelta(hours=6), amount=44, merchant_id="grocery_1")
    feat = compute_features(txn, ps, EntityRegistry(), txn["ts"])
    assert evaluate_rules(feat, txn) == []


def test_ordinary_payment_bursts_are_challenged_not_blocked():
    """Six normal-sized payments in five minutes to a merchant the customer
    already uses (bill splitting, UPI retries) is fast, but it is not card
    testing: step-up verification, never a hard block."""
    ps, now = _ps()
    ps.update(_txn(now - timedelta(days=1), amount=400.0, merchant_id="kirana_1", channel="online", mcc="grocery"))
    hits = []
    for i in range(7):
        t = now + timedelta(seconds=30 * i)
        txn = _txn(t, amount=450.0, channel="online", merchant_id="kirana_1", mcc="grocery")
        feat = compute_features(txn, ps, EntityRegistry(), t)
        hits = evaluate_rules(feat, txn, ps.velocity_baseline(t))
        ps.update(txn)
    codes = {h.code for h in hits}
    assert "card_testing" not in codes and "rapid_burst" in codes
    assert worst_severity(hits) == "challenge"


def test_velocity_rules_respect_a_customers_established_pace():
    """A customer who is routinely this busy (e.g. a shop's account) must not
    be challenged for their normal pace — but a burst well above it still is."""
    ps, now = _ps()
    day0 = now - timedelta(days=3)
    for d in range(3):                                   # three busy days, 14 txns in an hour each
        for k in range(14):
            ps.update(_txn(day0 + timedelta(days=d, minutes=4 * k), amount=300.0,
                           merchant_id=f"m{k % 3}", channel="online", mcc="retail"))
    base = ps.velocity_baseline(now)
    assert base["peak_1h"] >= 14 and base["n"] >= 20
    def one_hour(n_txn):
        p2, t0 = _ps()[0], now
        for attr in ("n", "peak_5m", "peak_1h"):
            setattr(p2, attr, getattr(ps, attr))
        last = None
        for k in range(n_txn):
            t = t0 + timedelta(minutes=60 * k / n_txn)
            txn = _txn(t, amount=300.0, merchant_id=f"m{k % 3}", channel="online", mcc="retail")
            feat = compute_features(txn, p2, EntityRegistry(), t)
            last = evaluate_rules(feat, txn, base)
            p2.update(txn)
        return {h.code for h in last}
    assert "velocity_1h" not in one_hour(14)             # their normal pace
    assert "velocity_1h" in one_hour(30)                 # > 1.5x their busiest spell
