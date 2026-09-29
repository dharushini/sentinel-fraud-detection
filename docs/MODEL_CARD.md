# Model card — Sentinel fraud scorer

Following the *Model Cards for Model Reporting* format (Mitchell et al., 2019).

## Overview

| | |
|---|---|
| **Task** | Binary risk score (0–1) for a card / account transaction, in real time |
| **Model** | Ensemble of three heads → single calibrated blended score:<br>• `HistGradientBoostingClassifier` (supervised) + isotonic calibration (sigmoid or none when calibration data is small)<br>• `IsolationForest` (unsupervised novelty)<br>• behavioural‑sequence divergence (smoothed unigram surprise + regime‑shift KL) |
| **Decision layer** | cost‑minimising threshold + deterministic rule engine → `ALLOW / REVIEW / CHALLENGE / BLOCK` |
| **Version** | 0.3 |
| **Owner** | hackathon project — defensive security use only |

## Intended use

- **In scope:** real‑time authorization‑time risk scoring for card‑present,
  card‑not‑present, ATM and account‑to‑account transfers; analyst triage;
  step‑up‑auth routing.
- **Out of scope:** credit decisioning, account‑opening / application fraud
  (different feature set — see the BAF dataset), AML transaction monitoring,
  any use as the *sole* basis for a legal or adverse action without human review.

## Training data

- **Demo model:** a seeded synthetic generator (`sentinel/datasets/synthetic.py`)
  — 400 customers in 8 Indian cities × 90 days ≈ 140k INR transactions, 0.8 %
  fraud, 7 attack playbooks (4 classic + 3 adversarial), plus fraud‑*looking*
  legitimate noise. Each customer has an age (18-25 / 26-40 / 41-60 / 60+ mix).
  Distribution shapes are grounded in public fraud statistics; it is **not** real
  customer data.
- **Real‑data validation:** ULB / MLG Credit Card Fraud (OpenML 1597, CC‑BY),
  284,807 real transactions — see [REAL_DATA_VALIDATION.md](REAL_DATA_VALIDATION.md).
- **Indian banking dataset (generated):** the shipped model does not transfer to it
  (ROC‑AUC ≈ 0.46); retrained on it, out‑of‑time ROC‑AUC 0.86 / PR‑AUC 0.28. Deploying
  institutions must retrain and validate on their own labelled history.
- **Feature pipeline is identical** offline and online; entity aggregates are
  built by replaying events in timestamp order. Fraud *outcomes* reach the
  entity statistics only after a delay (default 72 h, `SENTINEL_LABEL_DELAY_HOURS`)
  in both training and serving — a bank learns outcomes late, so features must
  not "know" that a transaction minutes ago was fraud.

## Evaluation

Full report: [EVALUATION.md](EVALUATION.md) (`python -m sentinel.eval`; also `GET /evaluation`).

- **Protocol:** classifier trained on a time‑ordered split (train 70 % / calibrate
  & threshold 15 % / out‑of‑time test 15 %); then the *full* pipeline (classifier +
  anomaly + rules + decision) is scored transaction‑by‑transaction on a **held‑out
  world** with a different seed (14,151 transactions, 188 fraud).
  Positive prediction = blocked or challenged.
- **Headline (held‑out, default operating point):** recall 94.7 %, precision
  43.5 %, genuine transactions stopped 1.65 % (blocked
  0.029 %), F1 59.6 %, MCC 0.636, ROC‑AUC
  0.9732, PR‑AUC 0.8143, ECE 0.00164 — with bootstrap 95 % CIs in the report.
- **Operating points:** the threshold is a business choice; e.g. 0.08 gives
  recall 92.0 % with 0.75 % of genuine transactions stopped at the same expected
  cost. `SENTINEL_MODEL_THRESHOLD` sets it without retraining.
- **Label‑timing sensitivity:** recall 98.4 % if outcomes were known
  instantly, 92.6 % with no outcome data at all.
- **Rules vs model:** rules alone catch 41.5 %, the model alone
  92.5 %, both 94.7 %.
- **Headline (real ULB, out‑of‑time, classifier only):** ROC‑AUC 0.92, PR‑AUC 0.65,
  Brier 0.0038 → 0.00047 after calibration (see REAL_DATA_VALIDATION.md; not re‑run
  for this version).
- **Adversarial:** the three evasion playbooks are reported separately in the
  report's per‑scenario table.
- **Latency:** P50 9 ms, P99 27 ms, ~100 txn/s/core on a 2‑core cloud VM
  (`python -m sentinel.audit`).
- **Fairness:** detection and false‑alarm rate by age bracket (FP‑rate gap
  0.57 %), home country, spend tier and channel. Per‑group
  recall rests on few fraud cases per group and is noisy; the FP‑rate gap is the
  reliable measure at this sample size.

## Limitations & risks

- Synthetic training data cannot capture every real fraud pattern; treat
  absolute numbers as illustrative until retrained on institutional data.
- The anomaly and sequence heads can drift as customer behaviour shifts — the
  built‑in **PSI monitor** exists to catch this; retrain on `alert`.
- Extreme class imbalance means PR‑AUC has high variance on small test slices.
- Blocking a legitimate transaction has real customer cost; the `CHALLENGE`
  tier and the analyst `REVIEW` queue exist so the model is rarely the *sole*
  actor on a decline. Only transactions that went through (allowed, or
  allowed-and-queued) update the behavioural profile — a blocked or
  verification-paused attempt never does, so an attacker cannot drag
  "normal" toward their behaviour.
- Customer age is used for reporting and fairness monitoring only; it is not
  a model feature.
- Feedback loop can encode analyst bias; dispositions should be sampled/audited.

## Ethical considerations

- Features are behavioural aggregates, not protected attributes. Home country is
  used only for *impossible‑travel* and *foreign‑transaction* signals, never as a
  standalone risk factor; the fairness audit slices on it to check for proxy
  effects.
- Analyst‑review cases can include a grouped counterfactual reference path for the
  calibrated classifier. It is hypothetical and non‑causal; it must not be presented
  as a recommended customer action or as proof that a feature caused the score.
