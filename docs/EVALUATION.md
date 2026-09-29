# Sentinel — Model Evaluation Report

_Generated 2026-09-27T23:22:33+00:00 by `python -m sentinel.eval`. Regenerate after any model, rule or threshold change._

## Test set
- held-out synthetic world (different seed from training): seed 1041 (training seed 42), 110 customers × 35 days
- 14,151 transactions, 188 fraud (1.33% prevalence)
- Definition: positive prediction = BLOCK or CHALLENGE; positive label = fraud

## Confusion matrix (operating point)

| | Predicted fraud (stopped) | Predicted legit (allowed/review) |
|---|---:|---:|
| **Actual fraud** | 178 (TP) | 10 (FN) |
| **Actual legit** | 231 (FP) | 13,732 (TN) |

## Metrics at the operating point

| Metric | Value |
|---|---:|
| Recall / detection rate | 94.7% (95% CI 91.4%–97.9%) |
| Precision | 43.5% (95% CI 39.3%–48.6%) |
| F1 | 59.6% (95% CI 55.2%–64.4%) |
| Specificity | 98.35% (95% CI 98.15%–98.55%) |
| False-positive rate (stopped) | 1.654% (95% CI 1.450%–1.855%) |
| False-positive rate (block only) | 0.029% |
| Matthews correlation (MCC) | 0.636 |
| Balanced accuracy | 96.5% |

## Threshold-free ranking quality (calibrated probability)
- ROC-AUC: **0.9732**
- PR-AUC: **0.8143** (random baseline = prevalence = 0.01329)

## Calibration
- Expected calibration error: 0.00164 · Brier score: 0.00407

| Predicted band | n | Mean predicted | Observed fraud rate |
|---|---:|---:|---:|
| 0.0–0.1 | 13,912 | 0.001 | 0.001 |
| 0.1–0.2 | 34 | 0.111 | 0.353 |
| 0.2–0.3 | 1 | 0.268 | 0.000 |
| 0.4–0.5 | 39 | 0.424 | 0.513 |
| 0.6–0.7 | 53 | 0.667 | 0.641 |
| 0.8–0.9 | 15 | 0.872 | 0.933 |
| 0.9–1.0 | 97 | 0.967 | 0.928 |

## Recall by fraud scenario

| Scenario | Fraud | Caught | Recall |
|---|---:|---:|---:|
| account_takeover | 23 | 23 | 100.0% |
| amount_just_under (adversarial) | 27 | 20 | 74.1% |
| bust_out | 22 | 19 | 86.4% |
| card_testing | 20 | 20 | 100.0% |
| geo_consistent_ato (adversarial) | 25 | 25 | 100.0% |
| slow_drip (adversarial) | 56 | 56 | 100.0% |
| stolen_card_geo | 15 | 15 | 100.0% |

## Fairness by customer age

| Age | Transactions | Fraud | Recall | False-positive rate |
|---|---:|---:|---:|---:|
| 18-25 | 1,929 | 14 | 100.0% | 1.410% |
| 26-40 | 5,522 | 109 | 97.2% | 1.589% |
| 41-60 | 3,931 | 20 | 100.0% | 1.636% |
| 60+ | 2,769 | 45 | 84.4% | 1.982% |

- False-positive-rate gap between age groups: 0.572%
- Recall gap between age groups: 15.6% — but each group has only 14–109 fraud cases, so this gap is mostly sampling noise; the false-positive-rate gap, measured over thousands of genuine transactions per group, is the reliable fairness measure at this sample size.

## Business impact
- Fraud value in test set: ₹192,234; prevented: ₹180,072 (93.7%)
- Decision mix: {'ALLOW': 13575, 'REVIEW': 167, 'CHALLENGE': 345, 'BLOCK': 64}

## Rules vs model vs both (same traffic)

Every transaction is also scored by the rules alone and the model alone. Here detection = blocked or challenged fraud; false positives = genuine transactions *blocked* (challenges not counted), matching the live dashboard strip.

| Policy | Fraud caught | Genuine blocked |
|---|---:|---:|
| Rules only | 41.5% | 0.029% |
| Model only | 92.5% | 0.000% |
| Sentinel (both) | 94.7% | 0.029% |


- Score drift (PSI) vs training reference: 0.0258 once customer profiles have warmed up (second half of the run); 0.1454 over the whole run, which includes the cold-start period where every test customer is new (PSI < 0.1 stable, 0.1–0.25 watch, > 0.25 investigate)

## Sensitivity to when fraud outcomes become known

Device / payee / merchant fraud statistics learn from confirmed fraud. A bank learns outcomes late (chargebacks, customer reports, analyst decisions), so the headline figures assume outcomes arrive 72 hours after the transaction — the model is also trained that way. The bounds:

| Outcomes known | Recall | Precision | False-positive rate | PR-AUC |
|---|---:|---:|---:|---:|
| instantly (upper bound) | 98.4% | 42.6% | 1.783% | 0.856 |
| after 72 h (headline) | 94.7% | 43.5% | 1.654% | 0.814 |
| never (no outcome data) | 92.6% | 51.9% | 1.153% | 0.790 |

## Operating points

Where to set the balance between fraud caught and genuine customers challenged is a business decision. Each row is the full pipeline re-run on the same held-out world with a different model threshold (rules and hard-block thresholds unchanged). Set `SENTINEL_MODEL_THRESHOLD` to choose one without retraining.

| Model threshold | Recall | Precision | Genuine stopped | Genuine blocked | False alarms per fraud caught | Expected cost* |
|---|---:|---:|---:|---:|---:|---:|
| 0.0476 (learned default) | 94.7% | 43.5% | 1.65% | 0.029% | 1.30 | 19.2 |
| 0.08 | 92.0% | 62.2% | 0.75% | 0.029% | 0.61 | 19.2 |
| 0.15 | 88.3% | 64.6% | 0.65% | 0.029% | 0.55 | 25.6 |
| 0.3 | 88.3% | 64.8% | 0.65% | 0.029% | 0.54 | 25.6 |
| 0.5 | 81.9% | 67.5% | 0.53% | 0.050% | 0.48 | 37.0 |

\* Missed frauds × 1 + genuine customers stopped × 0.04 — the cost ratio the default threshold is learned with (a missed fraud counted as 25× a false challenge). Thresholds with similar expected cost differ mainly in how the cost is split between fraud losses and customer friction — a choice for the bank's risk appetite. (Choosing a threshold from this table tunes on the test set, so re-validate the chosen operating point on fresh data.)

## Limitations
- The test world is synthetic (different seed, same generator). It measures generalisation to unseen customers and fraud episodes, not to a real bank's population; validate on the bank's own labelled history before production use.
- Real-data replays (UPI, PaySim) have no customer-age column, so age-based fairness is only measured on synthetic customers.
- Confidence intervals are percentile bootstrap over transactions and do not account for correlation between transactions of the same customer or fraud episode, so they are somewhat optimistic.
