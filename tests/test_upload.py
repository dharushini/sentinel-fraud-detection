"""Bulk CSV upload: row-level validation and end-to-end scoring."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from sentinel.upload import MAX_ROWS, TEMPLATE_CSV, parse_transactions_csv


def test_template_parses_cleanly():
    p = parse_transactions_csv(TEMPLATE_CSV)
    assert p["errors"] == [] and p["warnings"] == []
    assert len(p["events"]) == p["rows"] == 4
    assert p["has_labels"] and p["labelled"] == 3          # last row has a blank label
    ev = next(e for e in p["events"] if e["cust_id"] == "acct_2002")
    assert ev["amount"] == 45000.0 and ev["channel"] == "transfer"
    assert ev["cust_age"] == 68 and ev["label"] == 1 and ev["beneficiary"] == "mule_77"
    assert (ev["lat"], ev["lon"]) == (28.61, 77.21)          # Delhi from the city lookup
    assert [e["ts"] for e in p["events"]] == sorted(e["ts"] for e in p["events"])


def test_bad_rows_are_rejected_individually_with_reasons():
    csv = ("customer_id,Amount,Date,Channel,Category,Age\n"
           "a1,100,2026-09-01 10:00:00,online,retail,30\n"      # row 2 ok
           "a2,-5,2026-09-01 10:00:00,online,retail,30\n"       # row 3 negative
           "a3,abc,2026-09-01 10:00:00,online,retail,30\n"      # row 4 not a number
           "a4,100,yesterday,online,retail,30\n"                # row 5 bad date
           "a5,100,2026-09-01 10:00:00,teleport,retail,30\n"    # row 6 bad channel
           "a6,100,2026-09-01 10:00:00,online,retail,7\n"       # row 7 bad age
           ",100,2026-09-01 10:00:00,online,retail,30\n"        # row 8 no customer
           "a8,\"1,250.50\",01/09/2026 11:00:00,UPI,grocery,\n")  # row 9 ok: comma amount, alias
    p = parse_transactions_csv(csv)
    assert [e["cust_id"] for e in p["events"]] == ["a1", "a8"]
    assert [e["row"] for e in p["errors"]] == [3, 4, 5, 6, 7, 8]
    msgs = " | ".join(e["error"] for e in p["errors"])
    for needle in ("greater than 0", "not a number", "not recognised", "must be one of", "18-120", "cust_id is empty"):
        assert needle in msgs
    a8 = p["events"][1]
    assert a8["amount"] == 1250.5 and a8["channel"] == "transfer" and "cust_age" not in a8


def test_missing_required_column_is_a_file_level_error():
    with pytest.raises(ValueError, match="missing required column"):
        parse_transactions_csv("customer,price\na,1\n")


def test_missing_timestamps_are_spread_out_and_warned_about():
    """Rows without times must not all land on one instant — that would look
    exactly like a card-testing burst to the velocity features."""
    csv = "cust_id,amount\n" + "".join(f"c1,{100 + i}\n" for i in range(10))
    now = datetime(2026, 9, 27, 12, 0)
    p = parse_transactions_csv(csv, now=now)
    ts = [e["ts"] for e in p["events"]]
    assert len(set(ts)) == 10
    assert min(ts) >= datetime(2026, 9, 26, 12, 0) and max(ts) < now
    assert p["warnings"] and "timestamp" in p["warnings"][0].lower()


def test_row_limit_and_semicolon_delimiter():
    with pytest.raises(ValueError, match="limit"):
        parse_transactions_csv("cust_id,amount\n" + "c,1\n" * (MAX_ROWS + 1))
    p = parse_transactions_csv("cust_id;amount;timestamp\nx;10;2026-09-01 10:00:00\n")
    assert len(p["events"]) == 1 and p["errors"] == []


def test_excel_bom_is_ignored():
    p = parse_transactions_csv("﻿cust_id,amount\nx,10\n")
    assert len(p["events"]) == 1


def test_upload_endpoint_scores_and_reports_on_labelled_rows():
    from sentinel.main import app
    with TestClient(app) as client:
        before = client.get("/metrics").json()["processed"]
        v = client.post("/upload/transactions", json={"csv": TEMPLATE_CSV, "validate_only": True}).json()
        assert v["validate_only"] and v["scored"] == 0 and v["valid"] == 4
        assert client.get("/metrics").json()["processed"] == before     # nothing scored

        r = client.post("/upload/transactions", json={"csv": TEMPLATE_CSV})
        assert r.status_code == 200
        j = r.json()
        assert j["scored"] == 4 and sum(j["decision_mix"].values()) == 4
        assert j["labelled"]["labelled_rows"] == 3
        cm = j["labelled"]["confusion_matrix"]
        assert cm["tp"] + cm["fn"] == 1 and cm["fp"] + cm["tn"] == 2
        assert {r["age_bracket"] for r in j["results"]} <= {"18-25", "26-40", "41-60", "60+"}
        # every uploaded case is retrievable for the detail pane
        one = client.get(f"/cases/{j['results'][0]['id']}")
        assert one.status_code == 200 and one.json()["scenario"] == "upload"
        assert client.get("/cases/99999999").status_code == 404


def test_upload_endpoint_rejects_unusable_files_with_a_readable_message():
    from sentinel.main import app
    with TestClient(app) as client:
        r = client.post("/upload/transactions", json={"csv": "foo,bar\n1,2\n"})
        assert r.status_code == 422 and "missing required column" in r.json()["detail"]
        t = client.get("/upload/template.csv")
        assert t.status_code == 200 and t.text.startswith("cust_id,amount")


def test_replay_status_and_missing_dataset_message(monkeypatch, tmp_path):
    """Datasets aren't in the repository: /replay/status must report only what
    is really present, and replaying a missing one must say where to get it."""
    from sentinel import config
    from sentinel.main import app
    monkeypatch.setattr(config, "ROOT", tmp_path / "sentinel")
    with TestClient(app) as client:
        assert client.get("/replay/status").json()["available"] == []
        r = client.post("/replay", json={"schema_name": "india_bank", "limit": 5})
        assert r.status_code == 404 and "docs/REAL_DATA.md" in r.json()["detail"]
    ib = tmp_path / "data" / "india_bank"
    ib.mkdir(parents=True)
    (ib / "Transaction_Data_250k.csv").write_text("Transaction_ID\n")
    with TestClient(app) as client:
        assert [d["schema"] for d in client.get("/replay/status").json()["available"]] == ["india_bank"]
