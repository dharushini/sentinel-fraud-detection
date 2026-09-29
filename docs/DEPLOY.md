# Deploying Sentinel publicly

Sentinel is a **stateful FastAPI app** with a background traffic simulator and a
WebSocket feed. Pick a host that can run a long‑lived process:

| Host | Free? | Fits? | Effort |
|---|---|---|---|
| **Hugging Face Spaces** (Docker) | ✅ forever | ✅ full app, WebSocket, sleeps when idle | 2 min |
| **Render** (Docker web service) | ✅ (spins down after 15 min idle) | ✅ full app, WebSocket | 2 min |
| **Fly.io** | ✅ small allowance | ✅ full app, WebSocket | 3 min |
| **Vercel** | ✅ | ⚠️ *lite* mode only — no background loop, no WebSocket (poll‑driven) | 3 min |

A pre‑trained model (`sentinel/artifacts/model.joblib`, ~700 KB) is committed, so
every target starts instantly — no training on boot.

---

## Hugging Face Spaces  (recommended — free, permanent, ML‑friendly)

1. Create a Space → **SDK: Docker** → **blank**.
2. Push this repo to it (`git remote add space https://huggingface.co/spaces/<you>/sentinel && git push space main`).
3. Prepend this block to the top of `README.md` (Spaces requires it):

   ```yaml
   ---
   title: Sentinel Fraud Detection
   emoji: 🛡️
   colorFrom: blue
   colorTo: purple
   sdk: docker
   app_port: 8000
   pinned: false
   ---
   ```

Public URL: `https://<you>-sentinel.hf.space`.

---

## Render

1. New → **Blueprint**, point at this repo (it reads [`render.yaml`](../render.yaml)).
2. Deploy. Public URL: `https://sentinel-<hash>.onrender.com`.

Optionally add a free Render Redis instance and set `SENTINEL_REDIS_URL` so warm
behavioural state survives restarts and is shared across instances.

---

## Fly.io

```bash
fly launch --copy-config --now     # uses fly.toml
```

Public URL: `https://sentinel-fraud.fly.dev`.

---

## Vercel  (lite mode)

Vercel Python functions are request‑scoped: **no background loop, no WebSocket**.
`api/index.py` sets `SENTINEL_SERVERLESS=1`, which switches the app to
**poll mode** — the dashboard calls `POST /tick` for ambient traffic and attack
injections are scored synchronously. State is per‑instance and resets on cold
start (attach an external Redis via `SENTINEL_REDIS_URL` for continuity).

```bash
npm i -g vercel && vercel        # reads vercel.json + api/requirements.txt
```

`api/requirements.txt` pins scikit-learn / numpy / scipy / joblib to exactly the versions in the root `requirements.txt` — the committed model only unpickles reliably with those. Keep the two files in sync. The explainability path uses the existing classifier and adds no package dependency, keeping the function small. Counterfactual paths are hypothetical reference comparisons, not causal advice.

Public URL: `https://<project>.vercel.app`.

---

## Any Docker host

```bash
docker build -t sentinel .
docker run -p 8000:8000 sentinel
# or the 2-worker + Redis stack:
docker compose up --scale sentinel=2
```

## Security and configuration for a real deployment

The public demo is open by design. Before anything real goes behind it, set:

| Variable | Effect |
|---|---|
| `SENTINEL_BASIC_AUTH=user:password` | Every page and API route requires a login (HTTP Basic; the browser shows a prompt, then a 12-hour signed session cookie also covers the live WebSocket). `/health` stays open for platform health checks but reports only `{"status": ...}` to anonymous callers. Use a long random password and HTTPS (all hosts above serve HTTPS). |
| `SENTINEL_DISABLE_SIMULATOR=1` | No synthetic traffic or attack injection: the background simulator never starts and `/tick`, `/simulator/*`, `/replay/blended` return 403. The dashboard hides those controls and shows only real transactions (`/score`, CSV upload, `/replay`). |
| `SENTINEL_MODEL_THRESHOLD=0.08` | Operating point: the model probability at or above which a transaction is challenged. Unset = the learned cost-minimising value. See the trade-off table in [EVALUATION.md](EVALUATION.md). |
| `SENTINEL_LABEL_DELAY_HOURS=72` | How long after a transaction its confirmed fraud outcome reaches the entity statistics (default 72). |
| `SENTINEL_REDIS_URL` | Shared state across instances / restarts (queue, feedback, warm profiles). |
| `GROQ_API_KEY` | Enables the natural-language Q&A on a case ([QA_SETUP.md](QA_SETUP.md)); everything else runs without it. |

On Vercel: Project → Settings → Environment Variables, then redeploy. On Render /
Fly / Docker: set them as service environment variables.

Still the bank's responsibility before production: single sign-on / per-analyst
accounts instead of one shared login, rate-limiting, audit logging to the bank's
SIEM, data-residency review, and validation of the model on the bank's own
labelled history (see [MODEL_CARD.md](MODEL_CARD.md)).
