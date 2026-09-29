# 🛡️ Sentinel — Complete Guide

**Real-time AI fraud detection for Indian banking — scores every transaction in ~10 ms, decides *allow / flag / verify / block* before the money moves, and explains every decision in plain English.**

This guide covers everything: what Sentinel is, every feature, how it was built and tested, how to install it on **Mac** and **Windows** with copy-paste commands, how to **deploy it online**, and a **3-minute demo script** for judges.

---

## Contents

1. [The problem and our answer](#1-the-problem-and-our-answer)
2. [What makes Sentinel different](#2-what-makes-sentinel-different)
3. [Every feature, explained](#3-every-feature-explained)
4. [Live data — what "live" means here](#4-live-data--what-live-means-here)
5. [Measured results](#5-measured-results)
6. [Install on Windows (copy-paste)](#6-install-on-windows-copy-paste)
7. [Install on Mac (copy-paste)](#7-install-on-mac-copy-paste)
8. [Deploy online (Vercel, Render, Docker, Hugging Face)](#8-deploy-online)
9. [Configuration switches](#9-configuration-switches)
10. [3-minute demo script for judges](#10-3-minute-demo-script-for-judges)
11. [Judge Q&A — honest answers](#11-judge-qa--honest-answers)
12. [What we built, step by step](#12-what-we-built-step-by-step)
13. [How it works (architecture)](#13-how-it-works-architecture)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. The problem and our answer

Digital payments in India run at enormous volume, and fraud moves just as fast — account takeovers, card testing, mule accounts, scams aimed at older customers. Banks face two bad options:

- **Static rules** ("block anything over ₹50,000") are easy to explain but miss new attacks and annoy genuine customers.
- **Black-box AI** catches more but can't be explained to a customer, an auditor or the RBI.

**Sentinel combines both.** Three complementary AI detectors plus an auditable rules engine score each transaction as it happens, pick one of four actions, and attach a plain-English explanation to every decision.

| Action | What happens |
|---|---|
| ✅ **Allow** | Payment goes through |
| 👁️ **Flag (Review)** | Goes through, but queued for an analyst |
| 🔒 **Verify (Challenge)** | Paused for an OTP / step-up check |
| ⛔ **Block** | Stopped before the money leaves, customer alerted |

---

## 2. What makes Sentinel different

1. **Honest evaluation, built in.** One command produces a model-risk report: confusion matrix, confidence intervals, precision/recall, ROC/PR-AUC, calibration, per-attack recall and age fairness, on customers the model never saw. The same report is live on the dashboard.
2. **Explainable by design.** Every decision carries an AI summary, the rules that fired, the strongest signals, and a "what would change the decision" explorer.
3. **Fairness you can check.** Every transaction carries the customer's age group; a live panel shows fraud caught and false alarms per group.
4. **Realistic about time.** In real banks, confirmed fraud reaches the system days later (chargebacks). Sentinel is trained and runs the same way (72-hour delay), so its numbers aren't inflated by knowing answers early.
5. **Learns from verification, not from attackers.** A payment paused for OTP only becomes "normal" for the customer if they pass — so a fraudster's first attempt can't teach the system that their next one is fine.
6. **Judges speed against each customer's own pace.** A busy shopkeeper isn't challenged for their normal volume; a sudden burst well above a customer's usual pace is.
7. **Tested against attacks built to evade it.** Three "adversarial" fraud patterns are designed specifically to slip past rules, and are reported separately.
8. **The bank chooses its trade-off.** One setting moves the balance between fraud caught and customers bothered — with a table showing exactly what each choice costs.
9. **Runs anywhere for free.** Laptop, Docker, Render, Fly.io, Hugging Face, Vercel. All open-source, no paid APIs required.
10. **96 automated tests**, verified on Python 3.12 and 3.14.

---

## 3. Every feature, explained

### Live dashboard
- **Live transaction feed** — each transaction in ₹, colour-coded by decision, with an **age-group tag** (18-25 / 26-40 / 41-60 / 60+) and warning flags (new device, new country, new payee).
- **Headline numbers** — fraud caught, good customers stopped, money protected (₹), model health.
- **Simple / Analyst toggle** — Simple mode for anyone; Analyst mode adds risk scores, drift monitoring and charts.

### Click any transaction — the explanation pane
- **AI summary** — a plain-English analyst note ("This ₹38,000 transfer to a new payee was paused because…"), generated from the model's own evidence (no paid LLM needed).
- **Customer alert** — the exact SMS the customer would receive.
- **Verification (OTP) result** — for paused payments: *Customer passed* (the payment is learned as normal) or *Verification failed* (recorded as confirmed fraud).
- **"Was this the right call?"** — analyst feedback that updates the system.
- **What-if explorer** — slide the amount, toggle "new device / new payee / overnight", and watch the decision re-score live.
- **Entity graph** — which other customers share this device, payee or card (how fraud rings show up).
- **Signal breakdown** — behavioural, sequence and entity signals behind the score.
- **Robustness sweep** — how the decision reacts as one factor changes (is it a cliff an attacker could game?).
- **Ask a question** — optional natural-language Q&A about a case (needs a free Groq key; everything else works without it).

### Put transactions in
- **Simulate an attack** — 7 fraud patterns: account takeover, card testing, stolen card abroad, bust-out, plus 3 evasive ones (just under the limit, slow drip, stealth takeover), and a **fraud ring** across 4 customers.
- **Add a real transaction** — type one in by hand (amount in ₹, merchant, category, channel, city, **customer age**, device).
- **Upload a CSV** — up to 2,000 transactions at once. Bad rows are rejected with their row number and reason; valid rows are scored; flagged ones open straight into the explanation pane; download all results. If the file includes `is_fraud`, Sentinel reports how it did on them. A **template** is one click away.
- **Replay a dataset** — stream an Indian banking dataset, a UPI-style file or PaySim through the live pipeline (when the files are installed; see §4).

### Analysis panels
- **Who is being affected — by age** — per age group: transactions, fraud seen, share caught, false-alarm rate, average amount, and a fairness gap.
- **How good is the model? — independent test** — confusion matrix, fraud caught / precision / customers stopped with confidence intervals, ROC-AUC, PR-AUC, and recall per attack type.
- **Rules and AI, working together** — the same traffic scored by rules alone, AI alone and both.
- **Drift monitor** — warns when live traffic stops looking like what the model was trained on.

### Operations
- **Analyst review queue** — claim / release cases so several analysts can share the work.
- **Feedback loop** — analyst verdicts and chargebacks update entity risk and feed the next retrain.
- **Optional login** (`SENTINEL_BASIC_AUTH`) and **real-traffic-only mode** (`SENTINEL_DISABLE_SIMULATOR`).
- **REST API** with interactive docs at `/docs` — a bank's core system calls `POST /score` inline.

---

## 4. Live data — what "live" means here

We want to be precise, because judges will ask.

**What is live**
- **Real-time scoring.** Every transaction is scored the moment it arrives (~10 ms), streamed to the dashboard over a WebSocket (or polling on serverless hosts), and the metrics, age panel and drift monitor update continuously.
- **Live traffic generator.** A built-in simulator produces a continuous stream of realistic Indian banking activity — 400 customers in Mumbai, Delhi, Bengaluru, Chennai, Hyderabad, Kolkata, Pune and Ahmedabad, spending in ₹, each with an age — so the system is always doing something to watch.
- **Your own transactions, live.** Type one in, or upload a CSV, and it's scored in the same pipeline instantly.
- **Dataset replay.** Historical rows from independent datasets are streamed through the live engine as if they were arriving now.

**The datasets, honestly**
| Dataset | What it is | Used for |
|---|---|---|
| **Indian banking dataset** (`india_bank`) | 250,000 INR transactions, 25,000 customers **with real ages from the data**, cards, merchants across Indian states. *Generated data* (machine-made names, rule-derived labels). | Live replay with dataset ages; retraining test |
| **ULB credit card dataset** | **284,807 real** (anonymised) European card transactions — a published research benchmark | Proving the ML core works on real data |
| **UPI-style file** | UPI/Razorpay-shaped records. Its contents show it's simulator output. | Demo traffic only |
| **PaySim** | A well-known mobile-money simulator | Optional replay |

No bank data is used or needed: real customer data can't legally be shared at a hackathon. Sentinel is built so a bank plugs in its own history (see §5, "retrained on new data").

> Dataset files are **not** in the GitHub repository. The dashboard greys out replay buttons for datasets that aren't installed. To install them on your own machine, see `docs/REAL_DATA.md`.

---

## 5. Measured results

From `python -m sentinel.eval` on a **held-out** population the model never saw (14,151 transactions, 188 fraud). Full report: [`docs/EVALUATION.md`](EVALUATION.md).

| Metric | Value |
|---|---|
| Fraud caught (recall) | **94.7 %** (95 % CI 91.4–97.9 %) |
| Precision | 43.5 % — about 1.3 false alarms per fraud caught |
| Genuine transactions stopped | 1.65 % (outright **blocked: 0.03 %**) |
| ROC-AUC · PR-AUC | 0.973 · 0.814 (random guess = 0.013) |
| Fraud value prevented | 93.7 % |
| Calibration error | 0.0016 (predicted probabilities match reality) |
| Speed | ~9 ms median, ~27 ms at the 99th percentile |
| Rules alone · AI alone · both | 41.5 % · 92.6 % · **94.7 %** of fraud caught |

**The bank picks its operating point** (same test):

| Setting | Fraud caught | Genuine customers stopped |
|---|---|---|
| Default | 94.7 % | 1.65 % |
| `SENTINEL_MODEL_THRESHOLD=0.08` | 92.0 % | 0.75 % |

**On real data:** on the 284,807-transaction ULB benchmark the classifier reaches an out-of-time ROC-AUC of 0.92.

**On a new bank's data:** moved as-is to the Indian banking dataset, the model does *not* work (it's a different population). Retrained on that data with the same pipeline and no code changes, it reaches ROC-AUC **0.86**, about 20× better than random at finding fraud. That is the real-world workflow: a bank trains Sentinel on its own history, and Sentinel reports honestly how well that worked.

> The figures above are from the last full evaluation run. The final rule improvements shift them slightly; run `python -m sentinel.eval` (about 15 minutes) to refresh `docs/EVALUATION.md` and the dashboard panel.

---

## 6. Install on Windows (copy-paste)

**Needs:** Windows 10 or 11 and an internet connection for first setup. About 10 minutes.

### Step 1 — Install Python 3.12 and Git
Open **PowerShell** (press Start, type `PowerShell`, press Enter) and paste:

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
```

Accept any prompts. **Close PowerShell and open a new one** so the new programs are found.

> No `winget`? Download Python 3.12 from https://www.python.org/downloads/ — on the first installer screen **tick "Add python.exe to PATH"** — and Git from https://git-scm.com/download/win.
> Sentinel needs **Python 3.11 or newer**. Check with `py --version`.

### Step 2 — Download Sentinel and start it
Paste into the new PowerShell window:

```powershell
cd $HOME\Desktop
git clone https://github.com/Clementvsc/sentinel-fraud-detection.git
cd sentinel-fraud-detection
& ".\Start Sentinel.bat"
```

The first start installs packages (a few minutes), opens a **"Sentinel server"** window, and then opens the dashboard in your browser at **http://127.0.0.1:8000**.

- Keep the **Sentinel server** window open while using the site. Close it to stop.
- **Next time:** double-click **`Start Sentinel.bat`** in the `sentinel-fraud-detection` folder on the Desktop.

**No Git?** On https://github.com/Clementvsc/sentinel-fraud-detection click **Code → Download ZIP**, extract it, open the folder, and double-click **`Start Sentinel.bat`**.

> If Windows shows "Windows protected your PC", click **More info → Run anyway** (the script only sets up Python and starts the server).

---

## 7. Install on Mac (copy-paste)

**Needs:** macOS and internet for first setup. About 5–10 minutes.

### Step 1 — Install Homebrew, Python and Git (skip what you already have)
Open **Terminal** (Cmd + Space, type `Terminal`) and paste:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12 git
```

### Step 2 — Download Sentinel and start it

```bash
cd ~/Desktop
git clone https://github.com/Clementvsc/sentinel-fraud-detection.git
cd sentinel-fraud-detection
./run.sh
```

Open **http://127.0.0.1:8000**. Press **Ctrl + C** in Terminal to stop.
**Next time:** `cd ~/Desktop/sentinel-fraud-detection && ./run.sh`

`run.sh` automatically finds a suitable Python (3.11+), creates a private environment in `.venv`, installs the exact package versions, and starts the server. If it says Python is too old, run `brew install python@3.12` and try again.

### Optional — run the tests and the evaluation
```bash
source .venv/bin/activate
pytest -q                       # 96 automated tests
python -m sentinel.eval         # regenerate docs/EVALUATION.md (~15 min)
python -m sentinel.audit        # latency + fairness audit
```

---

## 8. Deploy online

First make sure your latest code is on GitHub. On the Mac, in the project folder:

```bash
git push
```

### Option A — Vercel (free, quickest)
Vercel runs Sentinel in "serverless" mode: the dashboard polls instead of streaming, and memory resets when the server sleeps. Everything else works.

**From the website (no command line):**
1. Go to https://vercel.com, sign in with GitHub.
2. **Add New… → Project → Import** `sentinel-fraud-detection`.
3. Framework preset: **Other**. Leave the build settings empty (the repo's `vercel.json` configures everything).
4. *(Recommended)* **Environment Variables**: add `SENTINEL_BASIC_AUTH` = `judge:choose-a-long-password` to password-protect it.
5. Click **Deploy**. You get a URL like `https://sentinel-fraud-detection.vercel.app`.

Every later `git push` redeploys automatically.

**From the command line instead:**
```bash
npm install -g vercel
vercel login
vercel --prod
```

### Option B — Render (free, full features including live streaming)
1. Go to https://render.com, sign in with GitHub.
2. **New → Blueprint** → select the repository. Render reads `render.yaml`.
3. Click **Apply**. URL: `https://sentinel-xxxx.onrender.com`. The free tier sleeps after 15 minutes idle; the first visit after that takes about a minute.

### Option C — Docker (any server or laptop)
```bash
docker build -t sentinel .
docker run -p 8000:8000 sentinel
```
Open http://localhost:8000. Two workers with shared state: `docker compose up --scale sentinel=2`.

### Option D — Hugging Face Spaces or Fly.io
See [`docs/DEPLOY.md`](DEPLOY.md). Fly.io: `fly launch --copy-config --now`.

---

## 9. Configuration switches

Set these as environment variables (on Vercel/Render: in the project's Environment settings).

| Variable | Default | What it does |
|---|---|---|
| `SENTINEL_BASIC_AUTH` | off | `user:password` — requires a login for the whole site and API |
| `SENTINEL_DISABLE_SIMULATOR` | off | `1` — real transactions only; hides attack buttons |
| `SENTINEL_MODEL_THRESHOLD` | learned | e.g. `0.08` — fewer customers challenged, slightly less fraud caught |
| `SENTINEL_LABEL_DELAY_HOURS` | 72 | when confirmed fraud outcomes reach the system |
| `SENTINEL_REDIS_URL` | off | shared memory across servers/restarts |
| `GROQ_API_KEY` | off | turns on natural-language Q&A about a case |

On a Mac or Linux terminal, for example: `SENTINEL_BASIC_AUTH=judge:secret ./run.sh`.
On Windows PowerShell: `$env:SENTINEL_BASIC_AUTH="judge:secret"; & ".\Start Sentinel.bat"`.

---

## 10. 3-minute demo script for judges

1. **Open the dashboard (15 s).** "Every row is a transaction in rupees, scored in about ten milliseconds, with the customer's age group. Green is allowed; anything else was flagged, paused for OTP, or blocked."
2. **Simulate an Account takeover (30 s).** Rows turn red. Click one: "Here's the plain-English reason, the SMS the customer gets, and the signals behind it."
3. **Simulate a Stealth takeover (30 s).** "Same attack from the customer's own phone and city, one failed login. No rule can see this. The AI catches the change in behaviour."
4. **What-if explorer (20 s).** Drag the amount down, toggle "new payee" off. "This is what would have changed the decision. Explainable, not a black box."
5. **Upload a CSV (30 s).** Upload a batch with a takeover buried in it. "Bad rows are rejected with reasons; the four takeover transfers come back flagged; with fraud labels Sentinel tells you exactly how it did."
6. **Scroll to the panels (30 s).** "An independent test on customers the model never saw: 94.7 % of fraud caught, 0.03 % of genuine customers blocked, with confidence intervals. And here's fairness by age: false-alarm rates within about half a percentage point across age groups."
7. **Close (15 s).** "The dashboard is the window; the product is one API call a bank's core system makes inline. Open-source, runs free, and it tells the truth about its own performance."

---

## 11. Judge Q&A — honest answers

**"Is this real bank data?"** No — no one can legally share that at a hackathon. The live demo uses a realistic Indian banking simulator. We validated the ML core on 284,807 real card transactions, and we showed that retraining on a new bank's data works (ROC-AUC 0.86). We're explicit about which datasets are generated.

**"Won't it annoy genuine customers?"** Outright blocks hit 0.03 % of genuine transactions. About 1.65 % are asked for OTP at the default setting, and the bank can halve that with one setting. Only payments that actually went through shape a customer's "normal".

**"How do you know the numbers aren't inflated?"** The test uses customers the model never saw. Fraud outcomes only reach the system 72 hours later, as in real life. We report confidence intervals and publish the limitations. During development our own evaluation caught three bugs that had inflated or distorted results, and we fixed them.

**"Is it fair?"** Every transaction has an age group, and false-alarm rates differ by about half a percentage point across groups. Age is used only for monitoring, never as a model input.

**"Why not deep learning?"** Gradient-boosted trees are what banks actually run in production: fast, accurate on tabular data, and explainable. Our ensemble of three different detectors is harder to evade than one model.

**"Is it production-ready?"** It's a production-shaped prototype: API, tests, monitoring, login, deploy configs, model card. Before real use a bank would retrain on its own data, connect single sign-on, and complete its model-risk validation. We document that list in `docs/DEPLOY.md`.

---

## 12. What we built, step by step

1. **Core engine** — FastAPI service; three detectors (gradient-boosted classifier with calibration, anomaly detector, behaviour-sequence model) blended into one risk score; rules engine; decision layer; explanations.
2. **Dashboard** — live feed, explanation pane, what-if explorer, entity graph, robustness sweep, analyst queue, Simple/Analyst modes, phone-friendly layout.
3. **India focus** — all amounts in ₹ with Indian number formatting and realistic rupee-scale spending; customers in Indian cities; Indian dataset adapters.
4. **Reliability fixes** — fixed a cold-start crash that showed "undefined" in the dashboard; fixed "everything gets blocked" (a corrupted saved state, and later a card-testing rule that blocked ordinary UPI bursts); fixed the committed model's library versions so deploys don't crash.
5. **Age categorisation** — every customer has an age; every transaction an age group; age panel with a fairness gap; age field in manual entry and CSV upload; real ages from the Indian banking dataset.
6. **Professional evaluation** — confusion matrix, confidence intervals, ROC/PR-AUC, calibration, per-attack recall, age fairness, label-timing sensitivity, rules-vs-AI comparison, operating points; the dashboard panel; `docs/EVALUATION.md`.
7. **Model-quality fixes the evaluation exposed:**
   - past fraud victims were being stopped for weeks (their own device and card stayed "tainted") → fixed; precision rose from 33 % to 49 %;
   - a paused-for-OTP fraud attempt taught the system the attacker's pattern was normal → fixed, and challenges now learn only when the customer passes verification;
   - fraud outcomes were being used instantly (unrealistic) → 72-hour delay in training and serving;
   - speed limits now compare each customer to their own normal pace.
8. **Manual data insertion** — single-transaction form plus CSV bulk upload with validation.
9. **Security and deployment** — optional login, real-traffic-only mode, pinned dependencies for Vercel, Python-version-aware installer, Windows launcher.
10. **Testing** — 96 automated tests; browser-tested in local, login-protected and serverless modes; verified on the exact Python version of the development Mac.

---

## 13. How it works (architecture)

```
 transaction ──▶ features ──────────────────────────────┐
  (₹, merchant,   • behaviour vs this customer's history  │
   device, city,  • entity risk (device/payee/merchant)   ▼
   age)           • fraud-ring links            ┌──────────────────┐
                  • behaviour sequence          │ 3 AI detectors    │──┐
                                                │ + rules engine    │  │
                                                └──────────────────┘  ▼
                                    decision: Allow / Flag / Verify / Block
                                    + plain-English explanation + customer SMS
                                                         │
          learns only from payments that went through ◀──┘──▶ live dashboard
          (and fraud outcomes that arrive 72 h later)          + API response
```

Stack: Python · FastAPI · scikit-learn · WebSocket · vanilla JS + Chart.js + Cytoscape · Docker · optional Redis. Key folders: `sentinel/` (engine, model, rules, API), `sentinel/frontend/` (dashboard), `tests/`, `docs/`.

---

## 14. Troubleshooting

| Problem | Fix |
|---|---|
| Windows: `py` or `git` not recognised | Close and reopen PowerShell after installing; or reinstall Python with **"Add to PATH"** ticked |
| Windows: "Setup did not finish" | Check that `py --version` shows 3.11 or newer and that you're online; run `Start Sentinel.bat` again |
| Mac: "Sentinel needs Python 3.11 or newer" | `brew install python@3.12`, then `./run.sh` again |
| Mac: `permission denied: ./run.sh` | `chmod +x run.sh` |
| Port 8000 already in use | Close the other Sentinel window, or on Mac run `lsof -ti:8000 \| xargs kill` |
| Replay buttons are greyed out | Normal — dataset files aren't in the repo; see `docs/REAL_DATA.md` |
| "Ask a question" says the key isn't set | Optional feature: set `GROQ_API_KEY` (free at console.groq.com) |
| Online version forgets data | Serverless hosts reset when idle; set `SENTINEL_REDIS_URL` or use Render/Docker |

---

*Built for the hackathon by Clement. Open-source, free to run, and honest about what it can and can't do.*
