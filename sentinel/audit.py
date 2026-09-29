"""Assurance checks: latency budget + fairness / disparate-impact.

    python -m sentinel.audit

* **Latency** — P50/P95/P99 of a full `engine.process` call and single-core
  throughput. A card network's authorization budget is ~100 ms; Sentinel should
  sit far under that.
* **Fairness** — detection rate and false-positive rate sliced by protected-ish
  attributes we *can* measure here (home country, customer spend tier, customer age, channel).
  Flags any slice whose FP rate is >1.25× the best slice (disparate impact).
"""
from __future__ import annotations

import time

import numpy as np

from . import config
from .datasets import SyntheticSource
from .engine import Engine
from .features import ProfileState
from .model import FraudModel


def _spend_tier(mean: float) -> str:
    # INR: customers' mean spend per transaction spans ~₹150-₹1,000 (see
    # datasets.synthetic.generate_customers); split it into thirds (log scale)
    return "low" if mean < 280 else "mid" if mean < 530 else "high"


def _run_world(engine: Engine, n_cust=140, days=40, seed=None):
    src = SyntheticSource(n_cust, days, seed=seed or (config.TRAIN_SEED + 555))
    engine.customers = {c.cust_id: c for c in src.customers}
    for c in src.customers:
        engine.profiles[c.cust_id] = ProfileState(
            c.cust_id, c.home_country, c.home_lat, c.home_lon, c.account_open)
    tiers = {c.cust_id: _spend_tier(c.spend_mean) for c in src.customers}
    rows, lat = [], []
    for ev in src.events():
        if ev["type"] == "login":
            ps = engine.profiles.get(ev["cust_id"])
            if ps:
                ps.add_login(ev["ts"], ev["success"], ev.get("device_id", ""))
            continue
        t0 = time.perf_counter()
        case = engine.process(ev)
        lat.append((time.perf_counter() - t0) * 1000.0)
        rows.append({
            "label": case["label"], "action": case["action"],
            "country": ev["country"], "channel": ev["channel"],
            "tier": tiers.get(ev["cust_id"], "mid"),
            "age": case.get("age_bracket", "unknown"),
        })
    return rows, np.asarray(lat)


def _slice_stats(rows, key):
    out = {}
    for v in sorted({r[key] for r in rows}):
        sub = [r for r in rows if r[key] == v]
        fraud = [r for r in sub if r["label"] == 1]
        legit = [r for r in sub if r["label"] == 0]
        det = np.mean([r["action"] in ("BLOCK", "CHALLENGE") for r in fraud]) if fraud else float("nan")
        fp = np.mean([r["action"] == "BLOCK" for r in legit]) if legit else 0.0
        out[v] = {"n": len(sub), "fraud": len(fraud), "detection": det, "fp_rate": fp}
    return out


def main() -> None:
    if not FraudModel.exists():
        raise SystemExit("train a model first: python -m sentinel.train")
    eng = Engine(FraudModel.load(), autosnapshot=False)
    rows, lat = _run_world(eng)

    print("\n=== LATENCY (full engine.process, single core) ===")
    for p in (50, 90, 95, 99):
        print(f"  P{p:<2d}  {np.percentile(lat, p):6.2f} ms")
    print(f"  mean {lat.mean():.2f} ms  ->  ~{1000/lat.mean():.0f} txn/s/core")
    print(f"  (card-network auth budget ~100 ms; headroom {100/np.percentile(lat,99):.0f}x)")

    print("\n=== FAIRNESS / DISPARATE IMPACT ===")
    for key in ("country", "tier", "age", "channel"):
        st = _slice_stats(rows, key)
        fps = [v["fp_rate"] for v in st.values() if v["n"] > 50]
        base = min(fps) if fps else 0.0
        print(f"\n  by {key}:")
        print(f"    {'slice':<10} {'n':>7} {'fraud':>6} {'detect':>8} {'FP rate':>9}  flag")
        for v_, s in st.items():
            ratio = (s["fp_rate"] / base) if base > 1e-9 else 1.0
            flag = "  ⚠ disparate" if (s["n"] > 50 and ratio > 1.25 and s["fp_rate"] > 0.002) else ""
            det = "  n/a" if s["fraud"] == 0 else f"{s['detection']*100:6.1f}%"
            print(f"    {str(v_):<10} {s['n']:>7} {s['fraud']:>6} {det:>8} "
                  f"{s['fp_rate']*100:>8.3f}%{flag}")
    print("\n  (a slice is flagged if FP rate > 1.25x the fairest slice and > 0.2%)")


if __name__ == "__main__":
    main()
