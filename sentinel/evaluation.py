"""Structured, reproducible model evaluation — the report a bank's model-risk
team would ask for before trusting a fraud model.

    python -m sentinel.eval            # prints it and writes the artifacts below

Everything is computed on a **held-out world**: a synthetic population with a
different seed from the one the model was trained on (different customers,
different fraud episodes), pushed transaction-by-transaction through the full
production pipeline (calibrated classifier + anomaly head + rules + decision
engine), exactly as live traffic is.

Definition used throughout, stated once so every number means the same thing:

    predicted positive  = the transaction was STOPPED (BLOCK or CHALLENGE)
    actual positive     = the transaction is labelled fraud

(The live dashboard's headline false-positive rate counts BLOCK only; this
report additionally gives the stricter "stopped" rate, since a challenged
genuine customer is still friction.)

Report sections: confusion matrix; precision / recall / specificity / F1 /
MCC / balanced accuracy at the operating point, each with a bootstrap 95% CI;
threshold-free ROC-AUC and PR-AUC on the calibrated probability; calibration
(reliability table, ECE, Brier); per-scenario recall; per-age-bracket
detection and false-alarm rates with a fairness gap; decision mix; money
protected; and the operating thresholds. Pure functions below are separately
unit-tested so the arithmetic can be trusted independently of the pipeline.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import config

REPORT_PATH = config.ROOT / "reports" / "eval_report.json"
STOP_ACTIONS = ("BLOCK", "CHALLENGE")
ADVERSARIAL = {"amount_just_under", "slow_drip", "geo_consistent_ato"}


# --------------------------------------------------------------------------- #
# pure metric functions
# --------------------------------------------------------------------------- #
def confusion(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    return {
        "tp": int(((y_true == 1) & (y_pred == 1)).sum()),
        "fp": int(((y_true == 0) & (y_pred == 1)).sum()),
        "tn": int(((y_true == 0) & (y_pred == 0)).sum()),
        "fn": int(((y_true == 1) & (y_pred == 0)).sum()),
    }


def _div(a: float, b: float) -> float | None:
    return (a / b) if b else None


def rates(cm: dict) -> dict:
    """Standard classification rates from a confusion matrix. A rate whose
    denominator is zero is None (undefined), not 0 — reporting 0.0 there
    would silently present 'no data' as a measured result."""
    tp, fp, tn, fn = cm["tp"], cm["fp"], cm["tn"], cm["fn"]
    precision = _div(tp, tp + fp)
    recall = _div(tp, tp + fn)
    specificity = _div(tn, tn + fp)
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and (precision + recall) else None)
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = ((tp * tn - fp * fn) / denom) if denom else None
    balanced = ((recall + specificity) / 2
                if recall is not None and specificity is not None else None)
    return {
        "precision": precision,
        "recall": recall,                       # = detection rate
        "specificity": specificity,
        "false_positive_rate": _div(fp, fp + tn),
        "false_negative_rate": _div(fn, fn + tp),
        "f1": f1,
        "mcc": mcc,
        "balanced_accuracy": balanced,
        "accuracy": _div(tp + tn, tp + fp + tn + fn),
    }


def bootstrap_ci(y_true, y_pred, metric: str, n_boot: int = 500,
                 seed: int = 0, alpha: float = 0.05) -> tuple[float, float] | None:
    """Percentile bootstrap CI for one rate. Fraud is rare, so a point
    estimate from a few hundred positives can move a lot between samples;
    the interval makes that uncertainty explicit instead of hiding it."""
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    n = len(y_true)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        v = rates(confusion(y_true[idx], y_pred[idx]))[metric]
        if v is not None:
            vals.append(v)
    if len(vals) < n_boot * 0.5:
        return None
    lo, hi = np.percentile(vals, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def calibration_table(proba, y, bins: int = 10) -> tuple[list[dict], float, float]:
    proba = np.asarray(proba, dtype=float)
    y = np.asarray(y, dtype=int)
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for i in range(bins):
        hi_ok = proba < edges[i + 1] if i < bins - 1 else proba <= 1.0
        m = (proba >= edges[i]) & hi_ok
        if not m.any():
            continue
        conf, acc, n = float(proba[m].mean()), float(y[m].mean()), int(m.sum())
        ece += n / len(y) * abs(acc - conf)
        rows.append({"lo": round(float(edges[i]), 2), "hi": round(float(edges[i + 1]), 2),
                     "n": n, "predicted": round(conf, 4), "observed": round(acc, 4)})
    brier = float(np.mean((proba - y) ** 2)) if len(y) else float("nan")
    return rows, float(ece), brier


def _r(x, d=4):
    return None if x is None else round(float(x), d)


# --------------------------------------------------------------------------- #
# the pipeline run
# --------------------------------------------------------------------------- #
def evaluate(model=None, n_customers: int = 110, days: int = 35,
             seed: int | None = None, n_boot: int = 500, progress=None,
             label_delay_hours: float | None = None) -> dict:
    """Score a held-out world through the full pipeline and build the report."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    from .datasets import SyntheticSource
    from .datasets.synthetic import age_bracket, AGE_BRACKETS
    from .drift import psi
    from .engine import Engine
    from .features import ProfileState
    from .model import FraudModel

    if model is None:
        if not FraudModel.exists():
            raise FileNotFoundError("no model artifact — run `python -m sentinel.train` first")
        model = FraudModel.load()
    seed = config.TRAIN_SEED + 999 if seed is None else seed
    if seed == config.TRAIN_SEED:
        raise ValueError("evaluation seed must differ from the training seed")

    engine = Engine(model, autosnapshot=False, label_delay_hours=label_delay_hours)
    src = SyntheticSource(n_customers, days, seed=seed)
    engine.customers = {c.cust_id: c for c in src.customers}
    for c in src.customers:
        engine.profiles[c.cust_id] = ProfileState(
            c.cust_id, c.home_country, c.home_lat, c.home_lon, c.account_open)

    events = src.events()
    n_txn = sum(e["type"] == "txn" for e in events)
    y, stopped, blocked, proba, risk, amounts, scen, ages = [], [], [], [], [], [], [], []
    actions = {"ALLOW": 0, "REVIEW": 0, "CHALLENGE": 0, "BLOCK": 0}
    done = 0
    for ev in events:
        if ev["type"] == "login":
            ps = engine.profiles.get(ev["cust_id"])
            if ps:
                ps.add_login(ev["ts"], ev["success"], ev.get("device_id", ""))
            continue
        case = engine.process(ev)
        done += 1
        if progress and done % 2000 == 0:
            progress(done, n_txn)
        y.append(int(case["label"]))
        stopped.append(int(case["action"] in STOP_ACTIONS))
        blocked.append(int(case["action"] == "BLOCK"))
        proba.append(float(case["fraud_proba"]))
        risk.append(float(case["risk"]))
        amounts.append(float(case["amount"]))
        scen.append(case.get("scenario", "legit"))
        ages.append(case.get("age_bracket") or age_bracket(case.get("cust_age", 35)))
        actions[case["action"]] += 1

    y_a, st_a, bl_a = np.asarray(y), np.asarray(stopped), np.asarray(blocked)
    pr_a, amt_a = np.asarray(proba), np.asarray(amounts)

    cm = confusion(y_a, st_a)
    op = rates(cm)
    cis = {m: bootstrap_ci(y_a, st_a, m, n_boot=n_boot)
           for m in ("precision", "recall", "specificity", "f1", "false_positive_rate")}
    block_cm = confusion(y_a, bl_a)

    both_classes = 0 < y_a.sum() < len(y_a)
    roc = float(roc_auc_score(y_a, pr_a)) if both_classes else None
    ap = float(average_precision_score(y_a, pr_a)) if both_classes else None
    calib_rows, ece, brier = calibration_table(pr_a, y_a)

    # per-scenario recall
    by_scen = {}
    for s in sorted({s for s, l in zip(scen, y) if l == 1}):
        idx = [i for i, (ss, l) in enumerate(zip(scen, y)) if ss == s and l == 1]
        caught = int(sum(stopped[i] for i in idx))
        by_scen[s] = {"fraud": len(idx), "caught": caught,
                      "recall": _r(caught / len(idx)), "adversarial": s in ADVERSARIAL}

    # per-age fairness
    by_age = []
    for label, _lo, _hi in AGE_BRACKETS:
        idx = np.asarray([i for i, a in enumerate(ages) if a == label], dtype=int)
        if idx.size == 0:
            by_age.append({"bracket": label, "transactions": 0})
            continue
        c = confusion(y_a[idx], st_a[idx])
        r = rates(c)
        by_age.append({"bracket": label, "transactions": int(idx.size),
                       "fraud": c["tp"] + c["fn"], **c,
                       "recall": _r(r["recall"]), "false_positive_rate": _r(r["false_positive_rate"], 5),
                       "precision": _r(r["precision"])})
    fprs = [a["false_positive_rate"] for a in by_age if a.get("false_positive_rate") is not None]
    recs = [a["recall"] for a in by_age if a.get("recall") is not None]

    fraud_amt = float(amt_a[y_a == 1].sum())
    saved = float(amt_a[(y_a == 1) & (st_a == 1)].sum())

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "kind": "held-out synthetic world (different seed from training)",
            "seed": seed, "train_seed": config.TRAIN_SEED,
            "customers": n_customers, "days": days,
            "transactions": int(len(y_a)), "fraud": int(y_a.sum()),
            "fraud_prevalence": _r(y_a.mean() if len(y_a) else None, 5),
        },
        "definition": "positive prediction = BLOCK or CHALLENGE; positive label = fraud",
        "label_delay_hours": (engine.label_delay.total_seconds() / 3600 if engine.label_delay else 0.0),
        "operating_point": {
            "block_risk": config.RISK_BLOCK, "challenge_risk": config.RISK_CHALLENGE,
            "review_risk": config.RISK_REVIEW,
            "model_probability_threshold": _r(model.label_threshold),
        },
        "confusion_matrix": cm,
        "metrics": {k: _r(v, 5) for k, v in op.items()},
        "confidence_intervals_95": {k: (None if v is None else [_r(v[0], 5), _r(v[1], 5)])
                                    for k, v in cis.items()},
        "block_only": {"confusion_matrix": block_cm,
                       "false_positive_rate": _r(rates(block_cm)["false_positive_rate"], 5),
                       "recall": _r(rates(block_cm)["recall"])},
        "threshold_free": {"roc_auc": _r(roc), "pr_auc": _r(ap),
                           "pr_auc_baseline": _r(y_a.mean() if len(y_a) else None, 5)},
        "calibration": {"ece": _r(ece, 5), "brier": _r(brier, 5), "table": calib_rows},
        "per_scenario": by_scen,
        "per_age_bracket": by_age,
        "fairness": {
            "false_positive_rate_gap": _r(max(fprs) - min(fprs), 5) if len(fprs) >= 2 else None,
            "recall_gap": _r(max(recs) - min(recs)) if len(recs) >= 2 else None,
        },
        "decision_mix": actions,
        # every transaction is also scored rules-only and model-only (shadow
        # policies); detection = BLOCK or CHALLENGE on fraud, FP = BLOCK on legit
        "policy_comparison": engine.metrics_snapshot()["policy_comparison"],
        "money": {"fraud_amount_inr": round(fraud_amt, 2), "prevented_inr": round(saved, 2),
                  "prevented_share": _r(saved / fraud_amt) if fraud_amt else None},
        # PSI over the whole run includes the warm-up period in which every
        # held-out customer is brand new (no behavioural history yet), which
        # shifts low-risk mass between the two lowest score bins. The second
        # half — profiles warmed — is the fair steady-state drift figure.
        "drift_psi_vs_training": _r(psi(model.meta.get("ref_scores", []), risk)),
        "drift_psi_after_warmup": _r(psi(model.meta.get("ref_scores", []), risk[len(risk) // 2:])),
        "training_meta": {k: model.meta.get(k) for k in
                          ("roc_auc", "pr_auc", "brier_calibrated", "cost_threshold", "split")},
    }


def save_report(report: dict, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    return path


def load_report(path: Path = REPORT_PATH) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
