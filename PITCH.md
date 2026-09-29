# Sentinel — pitch & demo script

## 30‑second pitch

> Banks lose **billions a year** to fraud. Static rules miss new attacks; a
> black‑box model can't be signed off. **Sentinel scores every transaction in
> ~10 ms** with **three complementary detection heads** — a calibrated
> gradient‑boosted classifier, an anomaly detector, and a behavioural‑sequence
> model — plus an auditable rules engine, and it **acts** (block / OTP‑challenge
> / analyst queue) *before the money leaves the account*. Every decision carries
> a **counterfactual evidence path**. It runs fully local on free open‑source software, and
> the ML core is validated on **284,807 real transactions**, not just our
> simulator.

---

## Every "weakness" is actually the point

| The obvious critique | Why it's a strength |
|---|---|
| *"It's synthetic data."* | Then we can open‑source the **entire** pipeline, data and eval — no bank will ever let you demo on real PII. Our generator reproduces **7 documented fraud typologies** and its distributions are grounded in published statistics. And the model core is **also trained & scored on the real ULB benchmark (284k transactions)** — one command, `sentinel.datasets.fetch ulb`. |
| *"That's not real AI / no deep learning."* | Gradient‑boosted trees are what **actually ships** at card networks and banks. We run an **ensemble of three diverse methods** — supervised + anomaly + sequence — which is *harder to evade* than one deep net and stays **fully explainable**. The architecture is model‑agnostic: a GRU/GNN head plugs into the same blend. |
| *"0.99 AUC is easy on this problem."* | We report the **time‑ordered, out‑of‑time** number, not the shuffled‑CV number everyone else quotes. On real data that's the difference between PR‑AUC **0.75 (random CV)** and **0.65 (honest)** — we show both and default to the honest one. |
| *"Any classifier catches obvious fraud."* | We **built three adversarial attacks to beat our own rules** and report each one separately: amount just under 74 %, geo consistent ato 100 %, slow drip 100 % caught on a held‑out test. Rules alone catch 41 % of all fraud; the full system 95 %. |
| *"Is it fast / production‑ready?"* | **P50 9 ms, P99 27 ms, ~100 txn/s/core** on a 2‑core cloud VM — ~4× under a card‑network auth budget. `docker compose up --scale sentinel=2` runs stateless workers sharing warm state via Redis. Restart‑safe snapshots, PSI drift alarms, cost knobs in one config file, a model card, and a fairness audit. |
| *"Won't it just block real customers?"* | Outright blocks hit **0.029 %** of genuine transactions; 1.65 % are asked to verify (OTP) at the default setting — **1.3 false alarm per fraud caught**. The bank chooses the trade‑off: at threshold 0.08, 0.75 % are asked to verify for 92 % recall. Only transactions that actually went through shape a customer's "normal", and age groups see false‑alarm rates within 0.57 % of each other. |

---

## Measured

Every number here comes from `python -m sentinel.eval` / `sentinel.audit`; the full
model‑risk report is [docs/EVALUATION.md](docs/EVALUATION.md).

**Held‑out synthetic world (different seed from training), full pipeline, default operating point** —
14,151 INR transactions, 188 fraud:

| | |
|---|---|
| Fraud caught (recall) | **94.7 %** (95 % CI 91.4 %–97.9 %) |
| Precision | 43.5 % (1.3 false alarm per fraud caught) |
| Genuine transactions stopped / blocked | 1.65 % / 0.029 % |
| ROC‑AUC · PR‑AUC · ECE | 0.9732 · 0.8143 · 0.00164 |
| Fraud value prevented | 93.7 % |
| Latency | P50 9 ms · P99 27 ms · ~100 txn/s/core (2‑core cloud VM) |
| Rules only · model only · both | **41.5 %** · **92.5 %** · **94.7 %** |

**Honest by construction:** fraud outcomes reach the model's entity statistics only
72 h after the fact (as chargebacks do); a customer's own past fraud never taints
their device or card; the test world is never used for training or tuning.

**Real data — ULB benchmark, 284,807 transactions, out‑of‑time (classifier):** ROC‑AUC
**0.92**, PR‑AUC 0.65, Brier 0.0038 → **0.0005** after calibration.

**Indian banking dataset (generated, 227k INR transactions with customer ages):** the shipped
model does not transfer (ROC‑AUC ≈ 0.46); retrained on it, the same pipeline reaches out‑of‑time
ROC‑AUC **0.86**, PR‑AUC 0.28 (≈ 20× random). The point for a bank: Sentinel is a pipeline you train
on your own history, and it tells you honestly how well that worked.

**Recall by attack type:** account_takeover 100 % · card_testing 100 % · geo_consistent_ato 100 % · slow_drip 100 % · stolen_card_geo 100 % · bust_out 86 % · amount_just_under 74 %.

---

## Live demo (3 minutes)

1. **The Policy A/B strip.** "Same live traffic, scored three ways. Rules alone
   catch about 41 % of fraud — the obvious cases, with auditable reasons. The
   model catches 93 %. Together: 95 %, and the rules give every hard block a
   reason a regulator can read."

2. **Click a flagged transaction.** Inspect the grouped reference path: it shows the
   calibrated classifier probability, the configured threshold, and which hypothetical
   feature families would need reference values for the score to cross that threshold.

3. **⚡ Account Takeover.** Rows go red. Click one: *"high‑value transfer right
   after 8 failed logins"*, calibrated proba 97 %, **BLOCK**, customer SMS shown.
   "Rules‑only *would* have caught this too — it's blatant."

4. **🎭 Stealth Takeover.** "Same attack, but from the customer's own phone, home
   city, one failed login — no geo or device tell." Click a caught one:
   *"rules‑only would ALLOW · model‑only would CHALLENGE · Sentinel CHALLENGE."*
   "This is the case rules physically cannot see. The **sequence head** catches
   the switch from groceries to wire transfers."

5. **🎭 Slow Drip.** "Small, in‑profile transfers to one mule, twice a day." Watch
   the **payee fraud‑rate** feature climb in the case view until it crosses the
   block threshold. "The graph learns the mule even though every single
   transaction looks normal."

6. **Click "Confirm fraud."** "That analyst verdict updated the entity's risk and
   will weight the next retrain. The system compounds."

7. **Scroll to "How good is the model?" and "Who is being affected — by age".**
   "Independent held‑out test: confusion matrix, confidence intervals, recall per
   attack type. And false‑alarm rates per age group — fairness you can check."

8. **Upload a CSV.** Drop in a batch with a takeover buried in it: bad rows are
   rejected with their row number, the takeover transfers come back flagged, and
   if the file has fraud labels Sentinel reports how it did on them.

9. **Close.** "The dashboard is a skin. The product is one `POST /score` a core
   banking system calls inline, in about ten milliseconds."

---

## Tech

Python · FastAPI · scikit‑learn (HistGradientBoosting + IsolationForest +
isotonic) · grouped counterfactual search · a pure‑Python sequence model · WebSocket · Docker + Redis.
`pytest` 80+ green, one command to run, **₹0** — all OSS, all local.
Dataset adapters for an Indian retail-banking dataset (with customer ages), UPI-style and PaySim files, Kaggle ULB / IEEE‑CIS / Sparkov (`docs/DATA.md`), CSV upload, a
model card, an evaluation report and a fairness audit.
