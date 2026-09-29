from datetime import datetime, timedelta

from sentinel.decision import shadow_actions
from sentinel.entities import EntityRegistry
from sentinel.features import FEATURE_COLUMNS, ProfileState, compute_features
from sentinel.model import ScoreBreakdown


def _txn(ts, **kw):
    d = dict(cust_id="C1", ts=ts, amount=40.0, mcc="grocery", channel="pos",
             merchant_id="m", beneficiary="", country="US", city="NYC",
             lat=40.71, lon=-74.01, device_id="d", card_bin="4")
    d.update(kw)
    return d


def test_sequence_features_present_and_bounded():
    ps = ProfileState("C1", "US", 40.71, -74.01, datetime(2024, 1, 1))
    feat = compute_features(_txn(datetime(2025, 6, 1, 12)), ps, EntityRegistry(),
                            datetime(2025, 6, 1, 12))
    for k in ("seq_surprise", "seq_new_token", "seq_repeat_5", "seq_regime_kl"):
        assert k in feat and 0.0 <= feat[k] <= 1.0
    assert set(feat) == set(FEATURE_COLUMNS)


def test_regime_shift_raises_sequence_signal():
    ps = ProfileState("C1", "US", 40.71, -74.01, datetime(2024, 1, 1))
    t = datetime(2025, 6, 1, 9)
    for i in range(30):                       # long history of ordinary grocery/pos
        ps.update(_txn(t + timedelta(days=i), amount=35 + i % 5))
    normal = compute_features(_txn(t + timedelta(days=40), amount=38), ps,
                              EntityRegistry(), t + timedelta(days=40))
    # abrupt switch to large wire transfers
    for i in range(4):
        ps.update(_txn(t + timedelta(days=41, hours=i), amount=900,
                       mcc="wire_transfer", channel="transfer", merchant_id=f"mule{i}"))
    shifted = compute_features(_txn(t + timedelta(days=41, hours=5), amount=900,
                              mcc="wire_transfer", channel="transfer", merchant_id="mule9"),
                              ps, EntityRegistry(), t + timedelta(days=41, hours=5))
    assert shifted["seq_regime_kl"] > normal["seq_regime_kl"]
    assert shifted["seq_surprise"] > normal["seq_surprise"]


def test_shadow_actions_ordering():
    # rules fire "block", model is quiet -> rules_only & full block, model_only allows
    from sentinel.rules import RuleHit
    sb = ScoreBreakdown(risk=0.05, fraud_proba=0.0, anomaly=0.1, top_features=[])
    hits = [RuleHit("x", "block", "boom")]
    sh = shadow_actions(sb, hits, 0.5)
    assert sh["rules_only"] == "BLOCK" and sh["full"] == "BLOCK"
    assert sh["model_only"] == "ALLOW"

    # model screams, no rules -> model_only & full block, rules_only allows
    sb2 = ScoreBreakdown(risk=0.95, fraud_proba=0.99, anomaly=0.9, top_features=[])
    sh2 = shadow_actions(sb2, [], 0.5)
    assert sh2["model_only"] == "BLOCK" and sh2["full"] == "BLOCK"
    assert sh2["rules_only"] == "ALLOW"


def test_engine_tracks_policy_comparison(trained_model):
    import random
    from sentinel.datasets import FRAUD_PLAYBOOKS, generate_customers, sample_legit_txn
    from sentinel.engine import Engine
    model, _ = trained_model
    eng = Engine(model, autosnapshot=False)
    rng = random.Random(0)
    cust = generate_customers(1, seed=3)[0]
    eng.customers[cust.cust_id] = cust
    eng.profiles[cust.cust_id] = ProfileState(
        cust.cust_id, cust.home_country, cust.home_lat, cust.home_lon, cust.account_open)
    base = datetime(2025, 6, 1, 9)
    for d in range(30):
        eng.process(sample_legit_txn(cust, base + timedelta(days=d, hours=3), rng))
    for e in FRAUD_PLAYBOOKS["account_takeover"](cust, datetime(2025, 7, 2, 2), random.Random(1)):
        if e["type"] == "txn":
            eng.process(e)
        else:
            eng.profiles[cust.cust_id].add_login(e["ts"], e["success"], e.get("device_id", ""))
    pc = eng.metrics_snapshot()["policy_comparison"]
    assert set(pc) == {"rules_only", "model_only", "full"}
    assert pc["full"]["detection_rate"] >= pc["rules_only"]["detection_rate"]
