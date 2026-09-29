"""Age-based transaction categorization.

Every customer sentinel generates now carries an `age`, and every event
`_mk_txn` produces is stamped with `cust_age`/`age_bracket`
(sentinel/datasets/synthetic.py). Engine.process() surfaces both on every
case it builds, for every entry point (score/tick/replay/replay-blended/
simulator-inject), which is what lets the dashboard and the fairness /
evaluation-metrics work break results down by age cohort.

Real-data feeds (csv_adapter.py's upi/paysim/sparkov/ieee/ulb) carry no
source age column, so those events reach Engine.process() with no
`cust_age` key at all. Engine.process() must still produce a valid,
*deterministic* (stable across repeated calls for the same cust_id) age
for those, rather than crashing on a missing field or emitting a fresh
random value every call.
"""
import random
from datetime import datetime

import pytest

from sentinel.datasets import generate_customers, sample_legit_txn, age_bracket, AGE_BRACKETS
from sentinel.datasets.csv_adapter import load_csv_events
from sentinel.datasets.synthetic import age_for_external_id
from sentinel.engine import Engine
from sentinel.features import ProfileState

_VALID_BRACKETS = {label for label, _lo, _hi in AGE_BRACKETS}


def test_generated_customers_have_plausible_ages():
    custs = generate_customers(200, seed=3)
    ages = [c.age for c in custs]
    assert all(isinstance(a, int) and 18 <= a <= 90 for a in ages)
    # not a single constant value / degenerate distribution
    assert len(set(ages)) > 10


def test_age_bracket_covers_the_generated_range():
    for c in generate_customers(200, seed=3):
        assert age_bracket(c.age) in _VALID_BRACKETS


def test_all_brackets_are_represented_in_a_realistic_population():
    """The weighted sampler must actually populate every cohort — a
    fairness-by-age-bracket report is meaningless if one bracket is always
    empty."""
    seen = {age_bracket(c.age) for c in generate_customers(400, seed=11)}
    assert seen == _VALID_BRACKETS


def test_synthetic_txn_carries_age_fields_matching_customer():
    cust = generate_customers(1, seed=5)[0]
    txn = sample_legit_txn(cust, datetime(2025, 6, 1, 10), random.Random(0))
    assert txn["cust_age"] == cust.age
    assert txn["age_bracket"] == age_bracket(cust.age)


def test_age_for_external_id_is_deterministic_and_valid():
    a1 = age_for_external_id("upi_device_abc")
    a2 = age_for_external_id("upi_device_abc")
    assert a1 == a2
    assert 18 <= a1 <= 90
    assert age_bracket(a1) in _VALID_BRACKETS
    # different ids are not all forced onto the same age
    assert len({age_for_external_id(f"upi_{i}") for i in range(30)}) > 1


def test_real_data_adapters_do_not_invent_an_age(tmp_path):
    """The upi adapter must NOT fabricate an age column — the source data has
    none, and Engine.process() is the single place that decides the
    fallback, so a real feed stays honestly age-less at the adapter level."""
    p = tmp_path / "upi.csv"
    p.write_text(
        "timestamp,amount,currency,upi_app,bank,device_fingerprint,status,is_suspicious\n"
        "2025-12-23T09:57:17.900016,1815.98,INR,Amazon Pay,Axis,device_abc,success,False\n"
    )
    ev = load_csv_events(str(p), "upi")
    assert ev and "cust_age" not in ev[0] and "age_bracket" not in ev[0]


@pytest.fixture(scope="module")
def engine(trained_model):
    model, _ = trained_model
    return Engine(model, autosnapshot=False)


def test_engine_surfaces_age_for_known_customer(engine):
    cust = generate_customers(1, seed=7)[0]
    engine.customers[cust.cust_id] = cust
    engine.profiles[cust.cust_id] = ProfileState(
        cust.cust_id, cust.home_country, cust.home_lat, cust.home_lon, cust.account_open)
    case = engine.process(sample_legit_txn(cust, datetime(2025, 6, 2, 9), random.Random(1)))
    assert case["cust_age"] == cust.age
    assert case["age_bracket"] == age_bracket(cust.age)


def _real_feed_txn(**kw):
    """A transaction shaped like csv_adapter.py's upi adapter output: no
    cust_age/age_bracket key anywhere."""
    base = {
        "type": "txn", "cust_id": "upi_device_unseen_42", "ts": datetime(2025, 6, 3, 11),
        "amount": 450.0, "mcc": "upi_transfer", "channel": "transfer",
        "merchant_id": "Axis_GPay", "beneficiary": "", "country": "IN", "city": "",
        "lat": 0.0, "lon": 0.0, "device_id": "device_unseen_42",
        "card_bin": "abc123", "label": 0,
    }
    base.update(kw)
    return base


def test_engine_falls_back_for_unknown_real_data_customer(engine):
    case = engine.process(_real_feed_txn())
    assert isinstance(case["cust_age"], int) and 18 <= case["cust_age"] <= 90
    assert case["age_bracket"] in _VALID_BRACKETS


def test_real_data_fallback_age_is_stable_across_calls(engine):
    """A customer cannot change age bracket between two transactions minutes
    apart — the fallback must be deterministic per cust_id, not re-rolled."""
    first = engine.process(_real_feed_txn(ts=datetime(2025, 6, 4, 10)))
    second = engine.process(_real_feed_txn(ts=datetime(2025, 6, 4, 10, 5)))
    assert first["cust_age"] == second["cust_age"]
    assert first["age_bracket"] == second["age_bracket"]


# --------------------------------------------------------------------------- #
# cohort breakdown (Engine.age_breakdown / GET /metrics/by_age)
# --------------------------------------------------------------------------- #
def test_age_breakdown_shape_and_empty_cohort_rates(engine):
    """An empty cohort must report None, never 0.0 — a bracket with no
    traffic is 'no data', and rendering it as a 0% false-alarm rate would
    read as a perfect score in exactly the fairness table meant to catch
    problems."""
    b = engine.age_breakdown()
    assert {"brackets", "total_scored", "fpr_gap", "note"} <= set(b)
    assert [x["bracket"] for x in b["brackets"]] == [lbl for lbl, _lo, _hi in AGE_BRACKETS]
    for row in b["brackets"]:
        if row["transactions"] == 0:
            assert row["detection_rate"] is None
            assert row["false_positive_rate"] is None
            assert row["avg_amount"] is None
        if row["legit"] == 0:
            assert row["false_positive_rate"] is None
        if row["fraud"] == 0:
            assert row["detection_rate"] is None


def test_age_breakdown_counts_match_the_cases_it_summarises(trained_model):
    """The table must be arithmetic over real scored cases, not an estimate:
    per-bracket totals have to reconcile exactly with the case buffer."""
    model, _ = trained_model
    eng = Engine(model, autosnapshot=False)
    custs = generate_customers(40, seed=21)
    rng = random.Random(4)
    for i, cust in enumerate(custs):
        eng.customers[cust.cust_id] = cust
        eng.profiles[cust.cust_id] = ProfileState(
            cust.cust_id, cust.home_country, cust.home_lat, cust.home_lon, cust.account_open)
        eng.process(sample_legit_txn(cust, datetime(2025, 7, 1, 9, i % 50), rng))

    b = eng.age_breakdown()
    cases = list(eng.cases)
    assert b["total_scored"] == len(cases) == sum(r["transactions"] for r in b["brackets"])
    for row in b["brackets"]:
        mine = [c for c in cases if c["age_bracket"] == row["bracket"]]
        assert row["transactions"] == len(mine)
        assert row["fraud"] + row["legit"] == row["transactions"]
        assert row["stopped"] == sum(c["action"] in ("BLOCK", "CHALLENGE") for c in mine)
        assert row["fraud_caught"] + row["legit_stopped"] == row["stopped"]


def test_metrics_by_age_endpoint_serves_the_breakdown():
    from fastapi.testclient import TestClient
    from sentinel.main import app

    with TestClient(app) as client:
        client.post("/tick", params={"n": 30})
        r = client.get("/metrics/by_age")
        assert r.status_code == 200
        j = r.json()
        assert j["total_scored"] > 0
        assert len(j["brackets"]) == len(AGE_BRACKETS)
        # whatever is reported must be JSON-clean (no NaN leaking into the UI)
        for row in j["brackets"]:
            for k in ("detection_rate", "false_positive_rate", "fraud_rate"):
                assert row[k] is None or 0.0 <= row[k] <= 1.0


def test_score_endpoint_honours_an_explicit_age_and_falls_back_without_one():
    """Covers the manual-entry path end to end, including the trap that an
    unset optional age must be dropped rather than passed through as None
    (which would both defeat the 'did the caller state an age' check and
    raise on int(None))."""
    from fastapi.testclient import TestClient
    from sentinel.main import app
    from sentinel.datasets.synthetic import age_for_external_id as _fallback

    with TestClient(app) as client:
        stated = client.post("/score", json={
            "cust_id": "manual_elder_test", "amount": 9000.0,
            "city": "Chennai", "country": "IN", "cust_age": 72}).json()
        assert stated["cust_age"] == 72 and stated["age_bracket"] == "60+"

        cid = "manual_no_age_test"
        unset = client.post("/score", json={
            "cust_id": cid, "amount": 1500.0, "city": "Mumbai", "country": "IN"}).json()
        assert unset["cust_age"] == _fallback(cid)
        again = client.post("/score", json={
            "cust_id": cid, "amount": 1600.0, "city": "Mumbai", "country": "IN"}).json()
        assert again["cust_age"] == unset["cust_age"]
