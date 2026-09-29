# 🛡️ Sentinel — AI‑Powered Banking Fraud Detection

> 📘 **New here? Start with the [complete guide](docs/HACKATHON_GUIDE.md)** — features, copy-paste install for Windows and Mac, deployment, and a 3-minute demo script.


**CDT‑04 · FinTech · Cybersecurity & Digital Trust**

Real‑time detection **and response** for banking fraud. Sentinel scores every
transaction in a few milliseconds against the customer's own behaviour *and* the
risk history of the merchant / device / payee it touches, then **blocks or
step‑up‑challenges** suspicious activity before the money moves — with
plain‑English evidence and a hypothetical counterfactual path for analyst review.

Everything here is **free and fully local** — open‑source libraries only, no paid
APIs, no accounts, one command to run.

---

## What's in the box

**Three complementary detection heads**, blended into one calibrated score — an
ensemble of *diverse methods* is harder to evade than any single deep net, and
every head is independently explainable:

| Head | Implementation | Catches |
|---|---|---|
| **Supervised** | `HistGradientBoostingClassifier` + **isotonic calibration** (sigmoid, or none, when there is too little data to calibrate reliably) | known fraud shapes |
| **Anomaly** | `IsolationForest` | novel patterns with no training examples |
| **Behavioural sequence** | smoothed per‑customer unigram surprise + regime‑shift KL divergence over discrete behaviour tokens | a grocery/POS customer suddenly wiring money; drum‑beat repetition |

fed by:

| Feature family | Detail |
|---|---|
| **Behavioural** (31) | amount vs personal norm, tempo, geo, device, login history, impossible‑travel speed |
| **Entity + graph** (13, [`entities.py`](sentinel/entities.py)) | Bayesian‑smoothed, time‑decayed **historical fraud rate** per merchant / card BIN / device / payee (device and card count *other* customers' fraud; outcomes arrive after a realistic delay), plus **graph fan‑in/out** (a mule collecting from 6 victims, one device on 6 logins → ring) |
| **Sequence** (4) | see above |

wrapped in:

| Layer | Implementation |
|---|---|
| **Rules** | deterministic tripwires — `known_bad_entity`, `fraud_ring`, impossible travel, velocity, login‑then‑drain |
| **Decision engine** | blended risk + **cost‑minimising threshold** (learned FN vs FP cost) → `ALLOW / REVIEW / CHALLENGE / BLOCK` + customer alert |
| **Explainability** | model-agnostic grouped counterfactual search plus clearly labeled, non-additive feature probes |
| **Drift monitoring** | live **PSI** of the risk‑score distribution vs a training reference → `warming / stable / watch / alert` |
| **Feedback loop** | `POST /feedback` records analyst dispositions + delayed chargebacks; retrain folds them in with **label‑maturity sample weighting** |
| **Policy A/B** | every transaction is *also* scored rules‑only and model‑only, live, so you can see the ensemble beat either alone |
| **State** | `StateStore` abstraction — in‑memory + atomic disk snapshot, restart‑safe; optional shared **Redis** via `SENTINEL_REDIS_URL` |
| **Assurance** | `python -m sentinel.audit` — latency percentiles + throughput, and fairness / disparate‑impact by country / spend tier / age / channel |
| **Evaluation** | `python -m sentinel.eval` — held-out confusion matrix, precision / recall / F1 / MCC with 95 % CIs, ROC & PR-AUC, calibration, per-attack recall, age fairness, operating points → [docs/EVALUATION.md](docs/EVALUATION.md) |
| **Data entry** | score one transaction by hand, or **upload a CSV** of up to 2,000 (row-level validation; reports accuracy if the file has fraud labels) |
| **Data** | seeded synthetic generator (Indian customers, INR, 7 playbooks, 3 adversarial) **or** a dataset — `python -m sentinel.datasets.fetch ulb` pulls **284,807 real transactions** cost‑free; adapters for an Indian retail-banking dataset with customer ages (`india_bank`), UPI-style and PaySim files (all generated — see [docs/REAL_DATA.md](docs/REAL_DATA.md)), and Kaggle IEEE‑CIS / Sparkov |

---

## Pipeline

```
                 ┌──────────────────────── Sentinel engine ────────────────────────┐
 transaction ───▶│ behavioural features ─┐                                          │
 + login events  │ entity / graph feats ─┼─▶ calibrated GBM ─┐                       │
                 │                        │   IsolationForest ─┼─▶ risk ─▶ decision ──┼─▶ ALLOW
                 │ deterministic rules ───┴───────────────────┘   (cost-based)       │─▶ REVIEW   → analyst queue
                 │                                                                   │─▶ CHALLENGE → OTP + alert
                 │ update profile + entity registry (if not blocked)                 │─▶ BLOCK     → stop + alert
                 │ feed drift/PSI monitor · snapshot state every N txns               │
                 └───────────────────────────────────────────────────────────────────┘
        feedback:  chargeback / analyst verdict ─▶ entity fraud counters + next retrain
```

No train/serve skew: the identical `compute_features()` runs offline and online;
entity aggregates are built by replaying events in time order.

---

## Model performance

All figures below are reproducible with `python -m sentinel.eval`, which writes the
full model-risk report to [docs/EVALUATION.md](docs/EVALUATION.md) (also served at
`GET /evaluation` and shown on the dashboard).

**Test set:** a *held-out* synthetic world — 110 customers × 35 days,
different seed from training, **14,151 transactions, 188 fraud**
(1.33 %) — scored transaction-by-transaction through the full pipeline
(classifier + anomaly head + rules + decision engine). Customers live in Indian cities and
spend in INR. A transaction counts as *stopped* if it is blocked **or** paused for verification.

| Metric (full pipeline, default operating point) | Value |
|---|---|
| Fraud caught (recall) | **94.7 %** (95 % CI 91.4 %–97.9 %) |
| Precision | **43.5 %** — 1.3 false alarms per fraud caught |
| Genuine transactions stopped | **1.65 %** (outright blocked: 0.029 %) |
| F1 · MCC | 59.6 % · 0.636 |
| ROC-AUC · PR-AUC | 0.9732 · 0.8143 (random = 0.01329) |
| Calibration error (ECE) · Brier | 0.00164 · 0.00407 |
| Fraud value prevented | 93.7 % of ₹192,234 |
| Score drift (PSI) once profiles warm | 0.0258 (stable < 0.1) |
| Latency P50 / P99 (`sentinel.audit`) | 9 ms / 27 ms per transaction on a 2-core cloud VM (~100 txn/s/core) |

**Fraud caught by attack type** (three *adversarial* playbooks are built to slip past rules):

| scenario | recall |
|---|---|
| account_takeover | 100 % (23/23) |
| card_testing | 100 % (20/20) |
| geo_consistent_ato *(adv.)* | 100 % (25/25) |
| slow_drip *(adv.)* | 100 % (56/56) |
| stolen_card_geo | 100 % (15/15) |
| bust_out | 86 % (19/22) |
| amount_just_under *(adv.)* | 74 % (20/27) |

**Operating point — the bank's choice.** The default threshold minimises expected cost with a
missed fraud weighted 25× a false challenge. Other points on the same test set:

| model threshold | recall | genuine stopped | precision |
|---|---|---|---|
| 0.0476 (default) | 94.7 % | 1.65 % | 43.5 % |
| 0.08 | 92.0 % | 0.75 % | 62.2 % |
| 0.15 | 88.3 % | 0.65 % | 64.6 % |
| 0.3 | 88.3 % | 0.65 % | 64.8 % |
| 0.5 | 81.9 % | 0.53 % | 67.5 % |

Set `SENTINEL_MODEL_THRESHOLD` to pick one without retraining (e.g. `0.08` halves customer
friction for ~3 points of recall at the same expected cost). Re-validate on fresh data.

**Fairness by age:** false-alarm rates differ by at most 0.57 % between
the 18-25 / 26-40 / 41-60 / 60+ groups. Live per-cohort numbers are on the dashboard
(`GET /metrics/by_age`); `python -m sentinel.audit` also slices by country, spend tier and channel.

**Honest-evaluation safeguards.** Device / payee / merchant fraud statistics only learn a
transaction's fraud outcome **72 hours later** (`SENTINEL_LABEL_DELAY_HOURS`), as chargebacks
and analyst decisions arrive at a real bank — in training and in serving. With outcomes known
instantly the same test would report 98.4 % recall; with no outcome data at all,
92.6 %. Transactions paused for verification never become part of a customer's
"normal" profile, and a customer's own past fraud never taints their own device or card.

> Synthetic test data — the numbers show the pipeline works and is honestly evaluated, not a
> production claim. Validate on the bank's own labelled history before go-live. The ML core is
> **also validated on real data**: `python -m sentinel.datasets.fetch ulb` → 284,807 real
> transactions → out-of-time ROC-AUC **0.92**; see
> [docs/REAL_DATA_VALIDATION.md](docs/REAL_DATA_VALIDATION.md). On the Indian banking dataset the
> shipped model does **not** transfer (classifier ROC-AUC ≈ 0.46); retrained on that data with the same
> pipeline it reaches out-of-time ROC-AUC **0.86** / PR-AUC 0.28 — train on your own history. See also
> [docs/DATA.md](docs/DATA.md) and
> [docs/MODEL_CARD.md](docs/MODEL_CARD.md).

---

## Quick start on Windows

1. Install **Python 3.11 or newer** (from python.org) if it is not already installed. Keep an internet connection for the first setup.
2. Double-click **Start Sentinel.bat** in this project folder. It creates a local Python environment, installs the project dependencies, starts the server in a second window, and opens the dashboard when it is ready.
3. Keep the **Sentinel server** window open while using the site. Close it or press Ctrl+C there to stop the server.

The first start can take a few minutes while Python packages install. If setup or startup fails, read the message in the Sentinel server window.

## Quick start on macOS or Linux

Requires **Python 3.11 or newer** (the committed model is pinned to scikit-learn
1.8.0, which needs 3.11+). `run.sh` picks the newest suitable `python3.x` it can
find and tells you what to install if there isn't one — on a Mac,
`brew install python@3.12` works.

From this project folder, run:

```bash
./run.sh
```

Then open **http://127.0.0.1:8000**. The dashboard opens in **Simple** mode
(plain‑language verdicts, AI summaries, big headline numbers); flip the header
toggle to **Analyst** for risk scores, counterfactual evidence, drift PSI and charts.

Containerised, with two workers sharing warm state via Redis:

```bash
docker compose up --scale sentinel=2
```

### Deploy it publicly (free)

| Host | Fit | |
|---|---|---|
| **Hugging Face Spaces** (Docker) | ✅ full app, WebSocket, free forever | `git push` + 8‑line README header |
| **Render** | ✅ full app, WebSocket | New → Blueprint (reads `render.yaml`) |
| **Fly.io** | ✅ full app, WebSocket | `fly launch --copy-config --now` |
| **Vercel** | ⚠️ lite mode — poll‑driven, no live push | `vercel` (reads `vercel.json`) |

A pre‑trained model is committed so every target boots instantly. Full steps in
[docs/DEPLOY.md](docs/DEPLOY.md). `SENTINEL_SERVERLESS=1` switches the app to
poll mode (`POST /tick`) for request‑scoped hosts.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m sentinel.train                       # -> sentinel/artifacts/model.joblib
python -m sentinel.eval                        # honest evaluation on an unseen world
python -m sentinel.audit                       # latency + fairness assurance
python -m sentinel.datasets.fetch ulb          # download 284,807 real transactions
uvicorn sentinel.main:app --port 8000
pytest                                         # 36 tests
```

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SENTINEL_BASIC_AUTH` | off | `user:password` — require a login for the dashboard and API |
| `SENTINEL_DISABLE_SIMULATOR` | off | `1` — real transactions only (no synthetic traffic / attack buttons) |
| `SENTINEL_MODEL_THRESHOLD` | learned | operating point — fraud caught vs customers challenged |
| `SENTINEL_LABEL_DELAY_HOURS` | 72 | when confirmed fraud outcomes reach the entity statistics |
| `SENTINEL_REDIS_URL` | off | shared state across instances |
| `GROQ_API_KEY` | off | natural-language Q&A on a case |

Details and a pre-production checklist: [docs/DEPLOY.md](docs/DEPLOY.md#security-and-configuration-for-a-real-deployment).

---

## Dashboard

- Live decision stream, colour‑coded, with risk bar.
- **Policy A/B strip** — rules‑only vs model‑only vs Sentinel, detection & FP,
  updating live on the same traffic.
- Click a transaction → **AI summary** (an analyst‑style narrative composed from
  the model's own feature probes + rules — no LLM, no cost), a **grouped
  counterfactual path** with the calibrated probability before and after the hypothetical
  reference changes, and single-feature probe bars. The path is a model comparison, not
  a causal explanation or customer action recommendation.
  **Confirm fraud / Mark legitimate** buttons that feed the feedback loop.
- **🔬 What‑if explorer** — an interactive counterfactual: drag the amount
  multiplier, toggle "unrecognised device / new payee / overnight / …", and the
  decision + risk + narrative re‑score live (`POST /whatif`). Shows what would
  have flipped the outcome.
- Header shows OOT metrics, calibrated Brier, the state backend, and a **live
  PSI drift indicator** (flips to `alert` during an attack wave).
- **⚡ / 🎭 attack buttons** — inject a full episode (4 classic + 3 adversarial
  playbooks) against a random real customer and watch Sentinel respond.
- **Age categorisation** — every transaction carries the customer's age bracket
  (18-25 / 26-40 / 41-60 / 60+); the **"Who is being affected — by age"** panel
  shows fraud caught and false-alarm rate per cohort, with a fairness gap.
- **Add a real transaction** (with optional customer age) or **upload a CSV** —
  bad rows are rejected with their row number and reason, valid rows are scored,
  and flagged ones open straight into the explanation pane. Download a template
  or the full results.
- **"How good is the model?"** panel — the held-out evaluation: confusion matrix,
  fraud caught / precision / genuine customers stopped with confidence
  intervals, ROC-AUC, PR-AUC, and recall per attack type.

---

## API

| Endpoint | Purpose |
|---|---|
| `POST /score` | score one transaction → decision, features, and `counterfactual` evidence for analyst-review cases |
| `GET /metrics` | detection / FP / exposure counters, per‑scenario recall |
| `GET /drift` | PSI drift status |
| `GET /metrics` → `policy_comparison` | live rules‑only vs model‑only vs full |
| `POST /whatif` | `{case_id, overrides}` — re‑score a past case with changed features → counterfactual action + narrative |
| `POST /feedback` | `{cust_id, ts, amount, label, kind}` — analyst/chargeback label |
| `GET /cases?only=alerts` | recent decisions (each carries `summary` + `shadows`) |
| `POST /simulator/inject/{scenario}` | play an attack episode |
| `POST /upload/transactions` | `{csv, validate_only}` — bulk-score a CSV (≤ 2,000 rows) with per-row errors |
| `GET /upload/template.csv` | upload template |
| `GET /cases/{id}` | one scored case |
| `GET /metrics/by_age` | decisions, fraud caught and false-alarm rate per age bracket |
| `GET /evaluation` | the held-out model evaluation report |
| `WS /ws/stream` | live push of decisions + metrics + drift |

```bash
curl -X POST localhost:8000/score -H 'content-type: application/json' -d '{
  "cust_id":"C00042","amount":4200,"mcc":"wire_transfer","channel":"transfer",
  "merchant_id":"acct_991","beneficiary":"acct_991","country":"UA",
  "lat":50.45,"lon":30.52,"device_id":"dev-new"}'
```

---

## Layout

```
sentinel/
  config.py         every threshold / weight / cost, one file
  datasets/         synthetic.py (7 playbooks) · csv_adapter.py (ULB/IEEE/Sparkov)
                    fetch.py (download real data) · load_events()
  features.py       ProfileState + compute_features() + sequence head  (shared train ↔ serve)
  entities.py       EntityRegistry — decayed fraud rates + graph fan-in/out
  model.py          HGB + isotonic calibration + cost threshold + IsolationForest
  explain.py        grouped counterfactual reference search
  rules.py          deterministic tripwires
  decision.py       score + rules → action + reasons + alert + shadow policies
  drift.py          PSI monitor
  feedback.py       analyst/chargeback store + label-maturity weights
  state.py          snapshot persistence (disk | Redis)
  engine.py         the real-time pipeline + metrics + policy A/B
  simulator.py      async ambient traffic (simulated clock) + attack injection
  audit.py          latency percentiles + fairness / disparate-impact
  train.py / eval.py
  main.py           FastAPI + dashboard
tests/              36 tests — features, entities, sequence, rules, model, drift,
                    feedback, state, policy A/B, csv adapters, fetch, audit,
                    full pipeline, API
Dockerfile · docker-compose.yml   two workers + Redis
docs/             DATA.md · REAL_DATA_VALIDATION.md · MODEL_CARD.md
```

---

## Next

- Sequence head (GRU/Transformer over each card's last N transactions).
- Graph neural net on the shared‑entity graph for ring detection at scale.
- Nightly scheduled retrain triggered by the PSI `alert` state.
- Per‑segment cost thresholds (a blocked $3 card‑test ≠ a blocked $3 000 payroll run).

*Defensive security use only.*
