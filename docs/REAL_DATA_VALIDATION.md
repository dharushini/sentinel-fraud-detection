# Validation on real transaction data

Sentinel's demo runs on a synthetic generator (it needs behavioural, geo, device
and entity fields that no anonymised public dataset carries). To show the **ML
core is not overfit to our own simulator**, we also train and evaluate it on a
real, published benchmark — downloaded cost-free, no account:

```bash
python -m sentinel.datasets.fetch ulb          # 284,807 real transactions from OpenML
SENTINEL_DATA=data/creditcard.csv SENTINEL_CSV_SCHEMA=ulb python -m sentinel.train
```

## Dataset

**ULB / MLG "Credit Card Fraud Detection"** — Dal Pozzolo, Caelen, Johnson &
Bontempi (2015), *Calibrating Probability with Undersampling for Unbalanced
Classification*, IEEE SSCI. OpenML dataset **1597**, licence **CC‑BY**.

- **284,807** real European card‑holder transactions, two days of September 2013
- **492** real frauds — **0.172 %** (extreme imbalance, the realistic regime)
- Features: `V1..V28` (PCA‑anonymised for privacy), `Time`, `Amount`

Because the features are PCA components, only the **calibrated GBM + isotonic +
IsolationForest** core runs here — the behavioural / entity / graph / sequence
features and the geo/velocity rules need raw fields the dataset doesn't expose.

## Results

_Measured with an earlier version of Sentinel and not re-run for the current one (the OpenML download isn't reachable from the environment used for this release); the ULB path is raw-feature mode, which the recent changes to entity statistics, rules and verification don't touch._

Sentinel reports the **honest, time‑ordered** number by default: train on the
first 70 % of the two days, calibrate on the next 15 %, test on the **last 15 %,
out‑of‑time** — i.e. predict transactions that happened *after* everything the
model saw.

| Evaluation | ROC‑AUC | PR‑AUC | Brier (raw → calibrated) |
|---|---|---|---|
| **Time‑ordered, out‑of‑time** (what Sentinel prints) | **0.924** | **0.652** | 0.0038 → **0.00047** (8× better) |
| Random 5‑fold CV (what most write‑ups report) | 0.968 ± 0.013 | 0.752 ± 0.029 | — |

At the cost‑minimising threshold on the out‑of‑time slice: **precision 0.63,
recall 0.69**.

### Reading the numbers

- The **0.10 PR‑AUC gap** between random CV and time‑ordered is the point:
  shuffling lets the model peek at the future. Fraud tactics drift *within* two
  days, so the out‑of‑time score is lower — and it's the one that matters in
  production. Sentinel is built to report that one.
- **Calibration cuts Brier ~8×** on real data too — the isotonic layer isn't a
  synthetic‑data artefact.
- The OOT test slice has only ~70 frauds, so PR‑AUC has real variance; treat it
  as "mid‑0.6s", not a point estimate.

## What this does and doesn't show

✅ The model core trains, calibrates and generalises on real, imbalanced,
adversarially‑drifting data with an honest protocol.

❌ It does **not** exercise the behavioural / entity / sequence layers or the
rules — those need a dataset with raw merchant / device / geo / timestamp fields
(e.g. Kaggle **Sparkov** or **IEEE‑CIS**; adapters included, see `DATA.md`).


---

# Indian banking dataset (`india_bank`)

A four-table Indian retail-banking dataset — 250k transactions, 25k customers
**with age**, 32k cards, 500 merchants; INR, Indian states and cities, RuPay /
Visa, UPI and net banking. It is **generated** data (machine-made names;
every fraud reason is 100 % fraud, i.e. the labels come from rules), not real
bank records — but it is the most India-specific source available and the only
one with per-customer ages. Adapter: `SENTINEL_CSV_SCHEMA=india_bank`
(successful transactions only: 227,821 rows, 3,147 fraud = 1.38 %).

## 1 — The shipped model, transferred as-is (no retraining)

Full pipeline over the most recent 40,000 transactions (2026-02-19 → 2026-08-18, 543 fraud):

| | |
|---|---|
| Recall · precision | 12.2% · 12.4% |
| Genuine transactions stopped | 1.18% |
| Classifier ROC-AUC · PR-AUC | 0.462 · 0.0134 (random = 0.0136) |
| Blended risk ROC-AUC | 0.688 |

**The transferred model does not work on this population** — its classifier is
no better than chance here. That is the expected result of moving a model to a
different population (different generator, very sparse customers — median 9
transactions over 3.6 years — and fraud driven by factors such as card status
that aren't model inputs), and it is exactly why a bank must train and validate
on its own history.

## 2 — Retrained on this dataset (same pipeline, no code changes)

```bash
SENTINEL_DATA=data/india_bank/Transaction_Data_250k.csv SENTINEL_CSV_SCHEMA=india_bank python -m sentinel.train
```

Time-ordered 70 / 15 / 15 split, out-of-time test slice:

| Metric | Value |
|---|---|
| ROC-AUC | **0.863** |
| PR-AUC | **0.283** (random = 0.014 → ~20× lift) |
| Brier, raw → calibrated | 0.176 → **0.0105** |
| Precision / recall at the cost-minimising threshold | 0.14 / 0.48 |

Retraining turns a chance-level model into a useful ranker, but the absolute
numbers are modest: this dataset's strongest fraud signals — card status
(expired / blocked / lost) and amount relative to credit limit — are not
Sentinel features yet. Adding institution-specific features like these is the
normal next step when onboarding a bank's data. (The shipped demo keeps the
synthetic-world model, because the live simulator and attack scenarios are
built around it.)
