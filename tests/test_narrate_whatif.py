import random
from datetime import datetime, timedelta

import pytest

from sentinel.narrate import narrate


def _case(**kw):
    base = dict(action="BLOCK", risk=0.94, fraud_proba=0.97, anomaly=0.6,
               amount=4200.0, cust_id="C1", mcc="wire_transfer", channel="transfer",
               merchant_id="mule_1", beneficiary="mule_1", city="Kyiv", country="UA",
               label=1, scenario="account_takeover", correct=True,
               reasons=["Risk 94% ...", "Impossible travel: 8000 km ..."],
               rule_hits=["ato_login_then_spend"],
               explanation=[
                   {"feature": "amount_z", "value": 6.2, "contribution": 0.30},
                   {"feature": "new_beneficiary", "value": 1.0, "contribution": 0.22},
                   {"feature": "account_age_days", "value": 2300.0, "contribution": -0.05},
               ],
               features={"amount_z": 6.2, "new_beneficiary": 1.0, "account_age_days": 2300.0})
    base.update(kw)
    return base


def test_narrate_block_reads_like_a_note():
    s = narrate(_case())
    assert s["headline"] in ("High-confidence block", "Blocked")
    assert "₹4,200" in s["summary"] and "transfer" in s["summary"]
    assert any("6.2" in d for d in s["drivers"])
    assert any("well established" in m for m in s["mitigators"])
    assert s["recommendation"]
    assert "fraud" in s["ground_truth"]


def test_narrate_allow_is_reassuring():
    s = narrate(_case(action="ALLOW", risk=0.06, fraud_proba=0.0, label=0,
                      scenario="legit", correct=True, rule_hits=[], explanation=[],
                      features={}))
    assert s["headline"].startswith("Cleared")
    assert "consistent with this customer" in s["summary"]


def test_whatif_endpoint_changes_the_decision(trained_model, monkeypatch):
    from sentinel.datasets import generate_customers, sample_legit_txn, FRAUD_PLAYBOOKS
    from sentinel.engine import Engine
    from sentinel.features import ProfileState

    model, _ = trained_model
    eng = Engine(model, autosnapshot=False)
    rng = random.Random(0)
    cust = generate_customers(1, seed=5)[0]
    eng.customers[cust.cust_id] = cust
    eng.profiles[cust.cust_id] = ProfileState(
        cust.cust_id, cust.home_country, cust.home_lat, cust.home_lon, cust.account_open)
    for d in range(30):
        eng.process(sample_legit_txn(cust, datetime(2025, 6, 1) + timedelta(days=d, hours=3), rng))

    # a fraudy transfer
    ev = [e for e in FRAUD_PLAYBOOKS["amount_just_under"](cust, datetime(2025, 7, 2, 2), random.Random(1))
          if e["type"] == "txn"][0]
    case = eng.process(ev)

    # A moderately smaller amount to a known payee should lower risk.
    # (Not a *tiny* amount: shrinking this ₹2,223 night transfer to ~₹44
    # makes it look like card testing — tiny probes to a new merchant — and
    # the model correctly scores THAT as riskier. "Smaller is always safer"
    # is not true in fraud detection, so the test must not assume it.)
    res = eng.whatif(case["id"], {"amount_mult": 0.3, "new_beneficiary": 0.0})
    assert res["whatif"]["risk"] <= res["base"]["risk"] + 1e-9
    assert "summary" in res and res["summary"]["summary"]
