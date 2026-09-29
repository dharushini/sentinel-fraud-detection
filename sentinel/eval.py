"""Honest evaluation on a fresh, unseen synthetic world.

    python -m sentinel.eval            # default held-out world
    python -m sentinel.eval --quick    # smaller world, for a fast sanity check

Runs every transaction of a held-out world (different seed from training)
through the *full* production pipeline and writes:

    sentinel/reports/eval_report.json   machine-readable (served at /evaluation)
    docs/EVALUATION.md                  human-readable model-risk summary

See sentinel/evaluation.py for the exact metric definitions.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import config
from .evaluation import evaluate, save_report

DOC_PATH = config.ROOT.parent / "docs" / "EVALUATION.md"


def _p(x, d=1):
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def _num(x, d=3):
    return "n/a" if x is None else f"{x:.{d}f}"


def _ci(r, k, d=1):
    c = r["confidence_intervals_95"].get(k)
    return "" if not c else f" (95% CI {_p(c[0], d)}–{_p(c[1], d)})"


def to_markdown(r: dict) -> str:
    m, cm, ds = r["metrics"], r["confusion_matrix"], r["dataset"]
    L = [
        "# Sentinel — Model Evaluation Report",
        "",
        f"_Generated {r['generated_at']} by `python -m sentinel.eval`. "
        "Regenerate after any model, rule or threshold change._",
        "",
        "## Test set",
        f"- {ds['kind']}: seed {ds['seed']} (training seed {ds['train_seed']}), "
        f"{ds['customers']} customers × {ds['days']} days",
        f"- {ds['transactions']:,} transactions, {ds['fraud']} fraud "
        f"({_p(ds['fraud_prevalence'], 2)} prevalence)",
        f"- Definition: {r['definition']}",
        "",
        "## Confusion matrix (operating point)",
        "",
        "| | Predicted fraud (stopped) | Predicted legit (allowed/review) |",
        "|---|---:|---:|",
        f"| **Actual fraud** | {cm['tp']:,} (TP) | {cm['fn']:,} (FN) |",
        f"| **Actual legit** | {cm['fp']:,} (FP) | {cm['tn']:,} (TN) |",
        "",
        "## Metrics at the operating point",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Recall / detection rate | {_p(m['recall'])}{_ci(r, 'recall')} |",
        f"| Precision | {_p(m['precision'])}{_ci(r, 'precision')} |",
        f"| F1 | {_p(m['f1'])}{_ci(r, 'f1')} |",
        f"| Specificity | {_p(m['specificity'], 2)}{_ci(r, 'specificity', 2)} |",
        f"| False-positive rate (stopped) | {_p(m['false_positive_rate'], 3)}{_ci(r, 'false_positive_rate', 3)} |",
        f"| False-positive rate (block only) | {_p(r['block_only']['false_positive_rate'], 3)} |",
        f"| Matthews correlation (MCC) | {_num(m['mcc'])} |",
        f"| Balanced accuracy | {_p(m['balanced_accuracy'])} |",
        "",
        "## Threshold-free ranking quality (calibrated probability)",
        f"- ROC-AUC: **{r['threshold_free']['roc_auc']}**",
        f"- PR-AUC: **{r['threshold_free']['pr_auc']}** "
        f"(random baseline = prevalence = {r['threshold_free']['pr_auc_baseline']})",
        "",
        "## Calibration",
        f"- Expected calibration error: {r['calibration']['ece']} · Brier score: {r['calibration']['brier']}",
        "",
        "| Predicted band | n | Mean predicted | Observed fraud rate |",
        "|---|---:|---:|---:|",
    ]
    for row in r["calibration"]["table"]:
        L.append(f"| {row['lo']:.1f}–{row['hi']:.1f} | {row['n']:,} | {row['predicted']:.3f} | {row['observed']:.3f} |")
    L += ["", "## Recall by fraud scenario", "", "| Scenario | Fraud | Caught | Recall |", "|---|---:|---:|---:|"]
    for s, v in r["per_scenario"].items():
        tag = " (adversarial)" if v["adversarial"] else ""
        L.append(f"| {s}{tag} | {v['fraud']} | {v['caught']} | {_p(v['recall'])} |")
    L += ["", "## Fairness by customer age", "",
          "| Age | Transactions | Fraud | Recall | False-positive rate |", "|---|---:|---:|---:|---:|"]
    for a in r["per_age_bracket"]:
        if not a.get("transactions"):
            L.append(f"| {a['bracket']} | 0 | – | – | – |")
            continue
        L.append(f"| {a['bracket']} | {a['transactions']:,} | {a['fraud']} | "
                 f"{_p(a['recall'])} | {_p(a['false_positive_rate'], 3)} |")
    f = r["fairness"]
    L += ["",
          f"- False-positive-rate gap between age groups: {_p(f['false_positive_rate_gap'], 3)}",
          f"- Recall gap between age groups: {_p(f['recall_gap'])}"
          + (f" — but each group has only {min(fr)}–{max(fr)} fraud cases, so this gap is mostly "
             "sampling noise; the false-positive-rate gap, measured over thousands of genuine "
             "transactions per group, is the reliable fairness measure at this sample size."
             if (fr := [a['fraud'] for a in r['per_age_bracket'] if a.get('fraud')]) and min(fr) < 100 else ""),
          "",
          "## Business impact",
          f"- Fraud value in test set: ₹{r['money']['fraud_amount_inr']:,.0f}; "
          f"prevented: ₹{r['money']['prevented_inr']:,.0f} ({_p(r['money']['prevented_share'])})",
          f"- Decision mix: {r['decision_mix']}",
          ]
    if r.get("policy_comparison"):
        pcmp = r["policy_comparison"]
        L += ["", "## Rules vs model vs both (same traffic)", "",
              "Every transaction is also scored by the rules alone and the model alone. Here "
              "detection = blocked or challenged fraud; false positives = genuine transactions "
              "*blocked* (challenges not counted), matching the live dashboard strip.", "",
              "| Policy | Fraud caught | Genuine blocked |", "|---|---:|---:|"]
        for name, label in (("rules_only", "Rules only"), ("model_only", "Model only"), ("full", "Sentinel (both)")):
            v = pcmp[name]
            L.append(f"| {label} | {_p(v['detection_rate'])} | {_p(v['false_positive_rate'], 3)} |")
        L.append("")
    L += ["",
          f"- Score drift (PSI) vs training reference: {r['drift_psi_after_warmup']} once customer "
          f"profiles have warmed up (second half of the run); {r['drift_psi_vs_training']} over the "
          "whole run, which includes the cold-start period where every test customer is new "
          "(PSI < 0.1 stable, 0.1–0.25 watch, > 0.25 investigate)",
          "",
          ]
    if r.get("label_timing_sensitivity"):
        L += ["## Sensitivity to when fraud outcomes become known", "",
              "Device / payee / merchant fraud statistics learn from confirmed fraud. A bank learns "
              "outcomes late (chargebacks, customer reports, analyst decisions), so the headline "
              f"figures assume outcomes arrive {r['label_delay_hours']:.0f} hours after the "
              "transaction — the model is also trained that way. The bounds:", "",
              "| Outcomes known | Recall | Precision | False-positive rate | PR-AUC |",
              "|---|---:|---:|---:|---:|"]
        for x in r["label_timing_sensitivity"]:
            L.append(f"| {x['outcomes_known']} | {_p(x['recall'])} | {_p(x['precision'])} | "
                     f"{_p(x['false_positive_rate'], 3)} | {_num(x['pr_auc'])} |")
        L.append("")
    if r.get("operating_points"):
        L += ["## Operating points", "",
              "Where to set the balance between fraud caught and genuine customers challenged is a "
              "business decision. Each row is the full pipeline re-run on the same held-out world "
              "with a different model threshold (rules and hard-block thresholds unchanged). "
              "Set `SENTINEL_MODEL_THRESHOLD` to choose one without retraining.", "",
              "| Model threshold | Recall | Precision | Genuine stopped | Genuine blocked | False alarms per fraud caught | Expected cost* |",
              "|---|---:|---:|---:|---:|---:|---:|"]
        n_fraud = r["dataset"]["fraud"]
        n_legit = r["dataset"]["transactions"] - n_fraud
        for o in r["operating_points"]:
            tag = " (learned default)" if o["learned"] else ""
            fa = "n/a" if o["false_alarms_per_fraud_caught"] is None else f"{o['false_alarms_per_fraud_caught']:.2f}"
            fn = n_fraud * (1 - (o["recall"] or 0))
            fp = n_legit * (o["false_positive_rate"] or 0)
            cost = config.COST_FALSE_NEGATIVE * fn + config.COST_FALSE_POSITIVE * fp
            L.append(f"| {o['threshold']:.4g}{tag} | {_p(o['recall'])} | {_p(o['precision'])} | "
                     f"{_p(o['false_positive_rate'], 2)} | {_p(o['block_fpr'], 3)} | {fa} | {cost:.1f} |")
        L += ["",
              f"\\* Missed frauds × {config.COST_FALSE_NEGATIVE:g} + genuine customers stopped × "
              f"{config.COST_FALSE_POSITIVE:g} — the cost ratio the default threshold is learned with "
              f"(a missed fraud counted as {config.COST_FALSE_NEGATIVE / config.COST_FALSE_POSITIVE:.0f}× a "
              "false challenge). Thresholds with similar expected cost differ mainly in how the cost is "
              "split between fraud losses and customer friction — a choice for the bank's risk appetite. "
              "(Choosing a threshold from this table tunes on the test set, so re-validate the chosen "
              "operating point on fresh data.)", ""]
    L += ["## Limitations",
          "- The test world is synthetic (different seed, same generator). It measures generalisation "
          "to unseen customers and fraud episodes, not to a real bank's population; validate on the "
          "bank's own labelled history before production use.",
          "- Real-data replays (UPI, PaySim) have no customer-age column, so age-based fairness is only "
          "measured on synthetic customers.",
          "- Confidence intervals are percentile bootstrap over transactions and do not account for "
          "correlation between transactions of the same customer or fraud episode, so they are "
          "somewhat optimistic.",
          ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="small world, fewer bootstrap samples")
    a = ap.parse_args()
    kw = dict(n_customers=40, days=20, n_boot=100) if a.quick else {}
    print("  scoring the held-out world through the full pipeline ...")
    r = evaluate(progress=lambda d, n: print(f"    {d:,}/{n:,}"), **kw)
    if not a.quick:
        # How much do the results depend on WHEN fraud outcomes become known?
        # Headline uses the configured realistic delay; show both bounds.
        sens = []
        for label, hours in (("instantly (upper bound)", 0.0),
                             (f"after {r['label_delay_hours']:.0f} h (headline)", None),
                             ("never (no outcome data)", 1e6)):
            rr = r if hours is None else evaluate(n_boot=20, label_delay_hours=hours)
            print(f"    label timing: {label} done")
            sens.append({"outcomes_known": label, "recall": rr["metrics"]["recall"],
                         "precision": rr["metrics"]["precision"],
                         "false_positive_rate": rr["metrics"]["false_positive_rate"],
                         "pr_auc": rr["threshold_free"]["pr_auc"]})
        r["label_timing_sensitivity"] = sens
        # Operating points: the same held-out world through the full pipeline
        # at several model thresholds, so a bank can choose its trade-off
        # between fraud caught and genuine customers challenged.
        from .model import FraudModel
        model = FraudModel.load()
        learned = model.label_threshold
        ops = []
        for thr in sorted({round(learned, 4), 0.08, 0.15, 0.30, 0.50}):
            model.threshold_override = thr
            rr = r if abs(thr - learned) < 1e-9 else evaluate(model, n_boot=20)
            print(f"    operating point {thr} done")
            ops.append({"threshold": thr, "learned": abs(thr - learned) < 1e-9,
                        "recall": rr["metrics"]["recall"], "precision": rr["metrics"]["precision"],
                        "false_positive_rate": rr["metrics"]["false_positive_rate"],
                        "block_fpr": rr["block_only"]["false_positive_rate"],
                        "false_alarms_per_fraud_caught": (rr["confusion_matrix"]["fp"] / rr["confusion_matrix"]["tp"]
                                                          if rr["confusion_matrix"]["tp"] else None)})
        model.threshold_override = None
        r["operating_points"] = ops
    p = save_report(r)
    md = to_markdown(r)
    if not a.quick:
        DOC_PATH.write_text(md)
    print(md)
    print(f"\n  wrote {p}" + ("" if a.quick else f" and {DOC_PATH}"))


if __name__ == "__main__":
    main()
