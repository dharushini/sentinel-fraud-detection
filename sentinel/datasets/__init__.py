"""Pluggable data sources for Sentinel.

Everything downstream consumes a single canonical *event* schema, so the model
does not care whether the events came from the synthetic generator or a real
transaction dump (Kaggle ULB / IEEE-CIS / Sparkov, an open-banking export, ...).

    txn event   = {type:"txn", ts, cust_id, amount, mcc, channel, merchant_id,
                   beneficiary, country, city, lat, lon, device_id, label}
    login event = {type:"login", ts, cust_id, device_id, success}
"""
from __future__ import annotations

from .. import config
from .synthetic import (
    SyntheticSource, generate_customers, FRAUD_PLAYBOOKS, sample_legit_txn,
    age_bracket, age_for_external_id, AGE_BRACKETS,
)


def load_events(source: str | None = None, **kw):
    """Return a time-sorted list of events from `source`.

    `source` is either ``"synthetic"`` or a path to a CSV file. A CSV path also
    needs a schema (``config.CSV_SCHEMA``: ``sparkov`` | ``ulb`` | ``ieee``).
    """
    source = source or config.DATA_SOURCE
    if source == "synthetic":
        return SyntheticSource(**kw).events()

    from .csv_adapter import load_csv_events
    return load_csv_events(source, schema=kw.get("schema", config.CSV_SCHEMA))


__all__ = [
    "load_events", "SyntheticSource", "generate_customers",
    "FRAUD_PLAYBOOKS", "sample_legit_txn",
    "age_bracket", "age_for_external_id", "AGE_BRACKETS",
]
