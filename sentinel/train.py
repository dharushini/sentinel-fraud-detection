"""Offline training.

    python -m sentinel.train                     # synthetic (default)
    SENTINEL_DATA=/path/fraudTrain.csv SENTINEL_CSV_SCHEMA=sparkov python -m sentinel.train
    SENTINEL_DATA=/path/creditcard.csv SENTINEL_CSV_SCHEMA=ulb     python -m sentinel.train

Replays the event stream through the *exact* feature pipeline used at serving
time (zero train/serve skew), builds entity/graph aggregates online, folds in
any recorded analyst feedback, applies label-maturity sample weights, and fits
the calibrated two-headed model with a **time-ordered** train/valid/test split.
"""
from __future__ import annotations

import time
from datetime import datetime

import numpy as np

from . import config
from .datasets import SyntheticSource, generate_customers
from .datasets import load_events
from .entities import EntityRegistry
from .feedback import FeedbackStore
from .features import FEATURE_COLUMNS, ProfileState, compute_features
from .model import train as fit_model


def _home(customers_by_id, cust_id, ev):
    c = customers_by_id.get(cust_id)
    if c is not None:
        return c.home_country, c.home_lat, c.home_lon, c.account_open
    return ev["country"], float(ev["lat"]), float(ev["lon"]), ev["ts"]


def replay(events: list[dict], customers_by_id: dict, use_entities: bool = True,
           label_delay_hours: float | None = None):
    """Yield (feature_row_dict_or_raw, label, ts) in event order.

    Entity fraud statistics learn a transaction's fraud outcome only
    `label_delay_hours` after it happened (default config.LABEL_DELAY_HOURS),
    exactly as the live engine does — so the model is trained on the same
    delayed-knowledge features it will see in production, instead of on
    features that already "know" a payment minutes ago was fraud."""
    import heapq
    from datetime import timedelta
    profiles: dict[str, ProfileState] = {}
    reg = EntityRegistry() if use_entities else None
    hours = config.LABEL_DELAY_HOURS if label_delay_hours is None else label_delay_hours
    delay = timedelta(hours=hours) if hours > 0 else None
    pending: list = []
    seq = 0
    raw_mode = any("raw_features" in e for e in events[:50] if e.get("type") == "txn")

    for ev in events:
        if ev.get("type") == "login":
            ps = profiles.get(ev["cust_id"])
            if ps is not None:
                ps.add_login(ev["ts"], ev["success"], ev.get("device_id", ""))
            continue

        if raw_mode:
            row = ev["raw_features"]
            yield row, int(ev.get("label", 0)), ev["ts"]
            continue

        if reg is not None:
            while pending and pending[0][0] <= ev["ts"]:
                rts, _s, keys = heapq.heappop(pending)
                reg.confirm_fraud(keys, rts)

        cid = ev["cust_id"]
        ps = profiles.get(cid)
        if ps is None:
            hc, hlat, hlon, opened = _home(customers_by_id, cid, ev)
            ps = profiles[cid] = ProfileState(cid, hc, hlat, hlon, opened)

        feat = compute_features(ev, ps, reg, ev["ts"])
        label = int(ev.get("label", 0))
        yield feat, label, ev["ts"]

        # mirror serving: a would-be-blocked fraud txn does not update "normal"
        if label == 0:
            ps.update(ev)
            if reg is not None:
                reg.observe(ev, 0, ev["ts"])
        elif reg is not None:
            # entity fraud stats *do* learn from fraud — once the outcome is known
            if delay is None:
                reg.observe(ev, 1, ev["ts"])
            else:
                reg.observe(ev, 0, ev["ts"])
                seq += 1
                keys = {k: ev.get(k, "") for k in
                        ("cust_id", "merchant_id", "card_bin", "device_id", "beneficiary")}
                heapq.heappush(pending, (ev["ts"] + delay, seq, keys))


def build_dataset(events: list[dict], customers_by_id: dict):
    gen = replay(events, customers_by_id)
    try:
        first_row, first_label, first_ts = next(gen)
    except StopIteration:
        raise ValueError("no transaction events to train on")
    cols = sorted(first_row.keys()) if "V1" in first_row else FEATURE_COLUMNS

    rows, y, ts = [[float(first_row[c]) for c in cols]], [first_label], [first_ts]
    for row, label, t in gen:
        rows.append([float(row[c]) for c in cols])
        y.append(label)
        ts.append(t)
    return np.asarray(rows, float), np.asarray(y, int), np.asarray(ts), cols


def temporal_split(ts: np.ndarray, frac=(0.70, 0.15, 0.15)):
    order = np.argsort(ts)
    n = len(ts)
    a, b = int(n * frac[0]), int(n * (frac[0] + frac[1]))
    return order[:a], order[a:b], order[b:]


def main() -> None:
    t0 = time.time()
    src = config.DATA_SOURCE
    if src == "synthetic":
        s = SyntheticSource(config.TRAIN_CUSTOMERS, config.TRAIN_DAYS, config.TRAIN_SEED)
        events = s.events()
        customers_by_id = {c.cust_id: c for c in s.customers}
        print(f"[train] synthetic world: {config.TRAIN_CUSTOMERS} customers x "
              f"{config.TRAIN_DAYS} days -> {len(events):,} events ({time.time()-t0:.1f}s)")
    else:
        events = load_events(src)
        customers_by_id = {}
        print(f"[train] loaded {len(events):,} events from {src} "
              f"(schema={config.CSV_SCHEMA}, {time.time()-t0:.1f}s)")

    fb = FeedbackStore()
    if len(fb):
        changed = fb.merge_labels(events)
        print(f"[train] folded in {len(fb)} feedback records ({changed} labels changed)")

    X, y, ts, cols = build_dataset(events, customers_by_id)
    print(f"[train] {len(y):,} transactions, {int(y.sum()):,} fraud "
          f"({y.mean()*100:.3f}%), {X.shape[1]} features")

    txn_events = [e for e in events if e.get("type") == "txn"]
    sw = FeedbackStore.sample_weights(txn_events) if txn_events and "raw_features" not in txn_events[0] else None

    split = temporal_split(ts)
    model, meta = fit_model(X, y, sample_weight=sw, feature_names=cols, split=split)
    model.save(config.MODEL_PATH)

    print("\n=== training report (metrics on the OUT-OF-TIME test slice) ===")
    for k in ("model", "split", "n_samples", "n_fraud", "fraud_rate", "roc_auc",
              "pr_auc", "brier_raw", "brier_calibrated", "cost_threshold",
              "precision_oot", "recall_oot"):
        print(f"  {k:18s}: {meta[k]}")
    print(f"\n  saved -> {config.MODEL_PATH}   ({time.time()-t0:.1f}s total)")


if __name__ == "__main__":
    main()
