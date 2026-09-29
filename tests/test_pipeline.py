"""End-to-end: the full engine pipeline on a warmed customer."""
import random
from datetime import datetime, timedelta

import pytest

from sentinel.datasets import FRAUD_PLAYBOOKS, generate_customers, sample_legit_txn
from sentinel.engine import Engine
from sentinel.features import ProfileState


@pytest.fixture(scope="module")
def engine(trained_model):
    model, _ = trained_model
    eng = Engine(model)
    rng = random.Random(0)
    cust = generate_customers(1, seed=99)[0]
    eng.customers[cust.cust_id] = cust
    eng.profiles[cust.cust_id] = ProfileState(
        cust.cust_id, cust.home_country, cust.home_lat, cust.home_lon, cust.account_open)
    t = datetime(2025, 6, 1, 9)
    for d in range(40):
        for h in range(4):
            eng.process(sample_legit_txn(cust, t + timedelta(days=d, hours=h * 3), rng))
    return eng, cust, rng


def test_normal_spend_allowed(engine):
    eng, cust, rng = engine
    # realistic cadence: a handful of ordinary purchases over a week
    allowed = 0
    for d in range(8):
        t = datetime(2025, 7, 5) + timedelta(days=d, hours=13)
        allowed += eng.process(sample_legit_txn(cust, t, rng))["action"] in ("ALLOW", "REVIEW")
    assert allowed >= 6


@pytest.mark.parametrize("scenario", ["account_takeover", "geo_consistent_ato",
                                      "amount_just_under", "card_testing"])
def test_attack_is_stopped(engine, scenario):
    eng, cust, rng = engine
    events = FRAUD_PLAYBOOKS[scenario](cust, datetime(2025, 7, 10, 2), random.Random(1))
    stopped = total = 0
    for e in events:
        if e["type"] == "login":
            eng.profiles[cust.cust_id].add_login(e["ts"], e["success"], e.get("device_id", ""))
            continue
        total += 1
        stopped += eng.process(e)["action"] in ("BLOCK", "CHALLENGE")
    assert stopped >= max(1, total // 2)          # majority of the episode caught


def test_drift_status_shape(engine):
    eng, *_ = engine
    d = eng.drift_status()
    assert d["state"] in ("warming", "stable", "watch", "alert") and "psi" in d
