"""FastAPI application — REST scoring API + live WebSocket feed + dashboard.

Run:  uvicorn sentinel.main:app
"""
from __future__ import annotations

import asyncio
import contextlib
import base64
import hashlib
import hmac
import json
from datetime import datetime

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .datasets import FRAUD_PLAYBOOKS
from .engine import Engine
from .model import FraudModel
from .simulator import Simulator


class TransactionIn(BaseModel):
    cust_id: str
    amount: float = Field(gt=0)
    mcc: str = "retail"
    channel: str = "online"
    merchant_id: str = "merchant_unknown"
    beneficiary: str = ""
    country: str = "IN"
    city: str = ""
    lat: float = 19.08          # Mumbai — matches the INR/India default above
    lon: float = 72.88
    device_id: str = "dev-unknown"
    card_bin: str = "?"
    ts: datetime | None = None
    label: int = 0
    # Optional: a manually-entered transaction may state the customer's age.
    # Left unset, Engine.process() resolves it (known Customer record, else a
    # deterministic per-cust_id fallback) exactly as it does for real-data
    # feeds, so age is never missing from a case but is never invented from
    # thin air on each call either.
    cust_age: int | None = Field(default=None, ge=18, le=120)


class SimConfigIn(BaseModel):
    rate: float | None = None
    fraud_rate: float | None = None
    running: bool | None = None


class ReplayIn(BaseModel):
    schema_name: str = "upi"       # sparkov | ieee | ulb | upi | paysim (see datasets/csv_adapter.py)
    path: str | None = None        # defaults to data/<schema_name>.csv
    limit: int = 200               # how many rows to replay this call
    speed: int = 15                # rows scored per broadcast batch (visual pacing only)


class BlendIn(BaseModel):
    real_schema: str = "upi"       # which real dataset to draw from (datasets/csv_adapter.py)
    real_path: str | None = None   # defaults to data/<real_schema>.csv
    limit: int = 200               # total transactions in the combined batch
    real_share: float = 0.5        # fraction of `limit` drawn from the real dataset


class WhatIfIn(BaseModel):
    case_id: int
    overrides: dict[str, float] = {}     # feature name -> value, plus "amount_mult"


class FeedbackIn(BaseModel):
    cust_id: str
    ts: datetime
    amount: float
    label: int = Field(ge=0, le=1)
    kind: str = "disposition"          # disposition | chargeback
    note: str = ""
    analyst: str = "unassigned"        # who resolved it — for the shared case queue


class QueueClaimIn(BaseModel):
    analyst: str = Field(min_length=1)


class QAIn(BaseModel):
    case_id: int
    question: str = Field(min_length=1, max_length=500)


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        if not self._clients:
            return
        payload = json.dumps(message, default=str)
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                self.disconnect(ws)


app = FastAPI(title="Sentinel — AI Banking Fraud Detection", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
hub = Hub()
STATE: dict = {}

# ---- optional HTTP Basic auth (SENTINEL_BASIC_AUTH="user:password") -----------
_SESSION_COOKIE = "sentinel_session"


def _session_token() -> str:
    # derived from the configured secret: changing the password logs everyone out
    return hmac.new(config.BASIC_AUTH.encode(), b"sentinel-session-v1", hashlib.sha256).hexdigest()


def _is_authenticated(headers, cookies) -> bool:
    if not config.BASIC_AUTH:
        return True
    if hmac.compare_digest(cookies.get(_SESSION_COOKIE, ""), _session_token()):
        return True
    h = headers.get("authorization", "")
    if h[:6].lower() == "basic ":
        try:
            supplied = base64.b64decode(h[6:], validate=True).decode("utf-8")
        except Exception:
            return False
        return hmac.compare_digest(supplied.encode(), config.BASIC_AUTH.encode())
    return False


@app.middleware("http")
async def _require_auth(request, call_next):
    if not config.BASIC_AUTH:
        return await call_next(request)
    ok = _is_authenticated(request.headers, request.cookies)
    if not ok:
        if request.url.path == "/health":            # platform health checks
            return JSONResponse({"status": "ok" if STATE.get("engine") else "starting"})
        return Response("Authentication required", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Sentinel", charset="UTF-8"'})
    resp = await call_next(request)
    token = _session_token()
    if request.cookies.get(_SESSION_COOKIE) != token:
        # lets the browser's WebSocket (which can't send Basic auth) authenticate
        resp.set_cookie(_SESSION_COOKIE, token, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=12 * 3600)
    return resp


def _require_simulator() -> None:
    if not config.SIMULATOR_ENABLED:
        raise HTTPException(403, "the synthetic traffic simulator is disabled on this deployment "
                                 "(SENTINEL_DISABLE_SIMULATOR) — score real transactions via /score, "
                                 "CSV upload or /replay")


def _boot() -> None:
    """Build the engine + simulator. Idempotent; safe to call from the startup
    event or lazily on the first request (serverless has no lifespan)."""
    if STATE.get("engine"):
        return
    if not FraudModel.exists():
        print("[sentinel] no model artifact — training one now ...")
        from .train import main as train_main
        train_main()
    model = FraudModel.load()
    engine = Engine(model)
    engine.seed_population()
    sim = Simulator(engine, hub.broadcast)
    if config.SIMULATOR_ENABLED:
        sim.start()
    STATE.update(model=model, engine=engine, sim=sim)
    print(f"[sentinel] ready — {model.meta.get('model')} | "
          f"OOT ROC-AUC {model.meta.get('roc_auc')} | "
          f"state: {engine.store.backend} | serverless: {config.SERVERLESS}")


@app.on_event("startup")
async def _startup() -> None:
    await asyncio.get_event_loop().run_in_executor(None, _boot)
    sim: Simulator | None = STATE.get("sim")
    if sim and config.SIMULATOR_ENABLED:
        sim.start()      # (re)start the background loop from the async context


@app.on_event("shutdown")
async def _shutdown() -> None:
    sim: Simulator | None = STATE.get("sim")
    if sim:
        with contextlib.suppress(Exception):
            await sim.stop()
    eng: Engine | None = STATE.get("engine")
    if eng:
        with contextlib.suppress(Exception):
            eng.persist()


def _engine() -> Engine:
    if STATE.get("engine") is None:
        _boot()                       # lazy init for serverless / no-lifespan hosts
    return STATE["engine"]


def _sim() -> Simulator:
    """Same lazy-init guarantee as _engine() — _boot() always sets both
    together, but a request can arrive before startup has run (serverless
    cold start, or any host without a lifespan event), and STATE["sim"]
    read directly would then raise an unhandled KeyError -> 500 with no
    detail, which the dashboard would render as "undefined" wherever it
    expects a field from the response (e.g. inject's cust_id/victim_city)."""
    if STATE.get("sim") is None:
        _boot()
    return STATE["sim"]


@app.get("/health")
def health() -> dict:
    eng = STATE.get("engine")
    return {
        "status": "ok" if eng else "starting",
        "serverless": config.SERVERLESS,
        "simulator": config.SIMULATOR_ENABLED,
        "model": STATE["model"].meta if STATE.get("model") else None,
        "drift": eng.drift_status() if eng else None,
        "state_backend": eng.store.backend if eng else None,
        "feedback_records": len(eng.feedback) if eng else 0,
    }


@app.post("/score")
async def score(txn: TransactionIn) -> JSONResponse:
    payload = txn.model_dump()
    payload["ts"] = payload["ts"] or datetime.utcnow()
    # An unset age must be ABSENT, not present-as-None: Engine.process() keys
    # off `"cust_age" in txn` to decide whether the caller stated an age, and
    # a None left in the payload would both defeat that check and blow up on
    # int(None).
    if payload.get("cust_age") is None:
        payload.pop("cust_age", None)
    # a live transaction's fraud outcome isn't known yet: a CHALLENGE waits
    # for the bank's verification result (POST /cases/{id}/verification)
    payload["_label_known"] = False
    case = _engine().process(payload)
    await hub.broadcast({"type": "case", "case": case})
    return JSONResponse(case)


@app.post("/tick")
async def tick(n: int = 10) -> dict:
    """Generate + score `n` ambient transactions on demand. The dashboard calls
    this when the live WebSocket isn't available (serverless / restricted hosts)."""
    _require_simulator()
    sim: Simulator = _sim()
    cases = sim.tick(n)
    return {"cases": cases, "metrics": _engine().metrics_snapshot(),
            "drift": _engine().drift_status()}


REPLAY_SCHEMAS = ("india_bank", "upi", "paysim", "sparkov", "ieee", "ulb")


def _dataset_path(schema: str):
    """Where a replayable dataset lives by default (data/ is git-ignored, so
    these exist only where someone has put the files)."""
    from pathlib import Path
    data_dir = config.ROOT.parent / "data"
    candidates = {
        "india_bank": [data_dir / "india_bank" / "Transaction_Data_250k.csv"],
        "ulb": [data_dir / "ulb.csv", data_dir / "creditcard.csv"],
    }.get(schema, [data_dir / f"{schema}.csv"])
    return next((c for c in candidates if c.exists()), candidates[0])


@app.post("/replay")
async def replay(r: ReplayIn) -> dict:
    """Stream historical transactions from an independent dataset (not the
    synthetic simulator) through the exact same live scoring + broadcast path
    as /score (see datasets/csv_adapter.py for the schemas). The datasets are
    not part of the repository — put the files under data/ (docs/REAL_DATA.md)
    or pass `path`. None of the supported datasets is real bank data."""
    from pathlib import Path
    from .datasets.csv_adapter import load_csv_events

    csv_path = Path(r.path) if r.path else _dataset_path(r.schema_name)
    if not csv_path.exists():
        raise HTTPException(
            404,
            f"no '{r.schema_name}' dataset on this server (expected {csv_path}). "
            f"Dataset files aren't part of the repository — see docs/REAL_DATA.md "
            f"for where to get them and where to put them, or pass its path in this request.",
        )

    STATE.setdefault("_replay_cursor", {})
    cursor = STATE["_replay_cursor"].get(str(csv_path), 0)
    events = STATE.get("_replay_cache", {}).get(str(csv_path))
    if events is None:
        events = load_csv_events(str(csv_path), r.schema_name)
        STATE.setdefault("_replay_cache", {})[str(csv_path)] = events

    batch = events[cursor: cursor + max(1, min(r.limit, 2000))]
    STATE["_replay_cursor"][str(csv_path)] = cursor + len(batch)

    eng = _engine()
    cases = []
    for ev in batch:
        ev = dict(ev)
        ev["scenario"] = f"dataset:{r.schema_name}"
        case = eng.process(ev)
        cases.append(case)
        await hub.broadcast({"type": "case", "case": case})
    await hub.broadcast({"type": "metrics", "metrics": eng.metrics_snapshot(),
                          "drift": eng.drift_status()})
    return {
        "schema": r.schema_name, "source": str(csv_path),
        "replayed": len(cases), "cursor": STATE["_replay_cursor"][str(csv_path)],
        "total_rows": len(events),
        "exhausted": STATE["_replay_cursor"][str(csv_path)] >= len(events),
    }


@app.get("/replay/status")
def replay_status() -> dict:
    """Which datasets are present on this server and ready to replay."""
    found = []
    for schema in REPLAY_SCHEMAS:
        p = _dataset_path(schema)
        if p.exists():
            found.append({"schema": schema, "path": str(p), "size_mb": round(p.stat().st_size / 1e6, 1)})
    return {"available": found, "data_dir": str(config.ROOT.parent / "data")}


@app.post("/replay/blended")
async def replay_blended(r: BlendIn) -> dict:
    """Stream ONE combined transaction feed made of historical dataset rows
    (default: the UPI-style file) interleaved with fresh synthetic
    INR traffic from the same simulator the ambient feed uses — merged into
    a single chronological stream and scored through the exact same
    engine.process() path as everything else, one row at a time, so a
    failure on any single row is reported rather than silently dropped.

    This is deliberately NOT "two datasets glued end to end": every event
    (real or synthetic) is timestamped, the two sources are merge-sorted by
    that timestamp, and the result is scored in that single chronological
    order — the same way a real production feed would see real and any
    newly-onboarded traffic arrive interleaved, not as two separate blocks.
    """
    _require_simulator()
    import random
    from pathlib import Path
    from datetime import timedelta
    from .datasets.csv_adapter import load_csv_events
    from .datasets import generate_customers, sample_legit_txn

    csv_path = Path(r.real_path) if r.real_path else _dataset_path(r.real_schema)
    if not csv_path.exists():
        raise HTTPException(
            404,
            f"no '{r.real_schema}' dataset on this server (expected {csv_path}). "
            f"Dataset files aren't part of the repository — see docs/REAL_DATA.md.",
        )

    limit = max(1, min(r.limit, 2000))
    real_share = max(0.0, min(r.real_share, 1.0))
    n_real = round(limit * real_share)
    n_synth = limit - n_real

    STATE.setdefault("_replay_cursor", {})
    STATE.setdefault("_replay_cache", {})
    cache_key = str(csv_path)
    cursor = STATE["_replay_cursor"].get(cache_key, 0)
    events = STATE["_replay_cache"].get(cache_key)
    if events is None:
        events = load_csv_events(cache_key, r.real_schema)
        STATE["_replay_cache"][cache_key] = events

    real_batch = [dict(e) for e in events[cursor: cursor + n_real]]
    for e in real_batch:
        e["scenario"] = f"dataset:{r.real_schema}"
        e["_source"] = "real"
    STATE["_replay_cursor"][cache_key] = cursor + len(real_batch)

    # Fresh synthetic INR traffic, timestamped across the same window the
    # real batch spans (or "now" if the real batch is empty/exhausted) so the
    # merge-sort actually interleaves them instead of one block trailing the
    # other.
    eng = _engine()
    STATE.setdefault("_blend_customers", generate_customers(200, seed=config.RUNTIME_SEED + 7))
    STATE.setdefault("_blend_rng", random.Random(config.RUNTIME_SEED + 7))
    customers = STATE["_blend_customers"]
    rng = STATE["_blend_rng"]

    if real_batch:
        t_lo, t_hi = real_batch[0]["ts"], real_batch[-1]["ts"]
        span = max((t_hi - t_lo).total_seconds(), 60.0)
    else:
        t_lo, span = datetime.utcnow(), 3600.0

    synth_batch = []
    for _ in range(n_synth):
        cust = rng.choice(customers)
        offset = rng.uniform(0.0, span)
        ts = t_lo + timedelta(seconds=offset)
        ev = sample_legit_txn(cust, ts, rng)
        ev["scenario"] = "synthetic:inr"
        ev["_source"] = "synthetic"
        synth_batch.append(ev)

    combined = sorted(real_batch + synth_batch, key=lambda e: e["ts"])

    cases, errors = [], []
    real_scored = synth_scored = 0
    for i, ev in enumerate(combined):
        source = ev.pop("_source", "unknown")
        try:
            case = eng.process(dict(ev))
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller, never swallowed
            errors.append({"index": i, "cust_id": ev.get("cust_id"),
                            "scenario": ev.get("scenario"), "source": source, "error": str(exc)})
            continue
        cases.append(case)
        real_scored += source == "real"
        synth_scored += source == "synthetic"
        await hub.broadcast({"type": "case", "case": case})

    await hub.broadcast({"type": "metrics", "metrics": eng.metrics_snapshot(),
                          "drift": eng.drift_status()})

    return {
        "requested": limit, "real_requested": n_real, "synth_requested": n_synth,
        "real_scored": real_scored, "synth_scored": synth_scored,
        "scored": len(cases), "errors": errors,
        "real_source": str(csv_path), "real_cursor": STATE["_replay_cursor"][cache_key],
        "real_total_rows": len(events),
        "real_exhausted": STATE["_replay_cursor"][cache_key] >= len(events),
    }


@app.get("/metrics")
def metrics() -> dict:
    return _engine().metrics_snapshot()


@app.get("/metrics/by_age")
def metrics_by_age() -> dict:
    """Decision and outcome stats per customer age cohort — both the
    operational view (where the fraud is, per bracket) and the fairness view
    (whether legitimate customers in one cohort are stopped more often than
    another). See Engine.age_breakdown for what each field means."""
    return _engine().age_breakdown()


class UploadIn(BaseModel):
    csv: str = Field(min_length=1)
    validate_only: bool = False     # check the file without scoring anything


@app.get("/upload/template.csv")
def upload_template():
    from fastapi.responses import Response
    from .upload import TEMPLATE_CSV
    return Response(TEMPLATE_CSV, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="sentinel_upload_template.csv"'})


@app.post("/upload/transactions")
async def upload_transactions(u: UploadIn) -> dict:
    """Bulk-score a CSV of transactions (sent as text). Invalid rows are
    rejected individually with row number + reason; valid rows are scored in
    time order through the same pipeline as live traffic and appear in the
    dashboard feed. If the file carries fraud labels, the response includes
    how Sentinel did on them (confusion matrix, recall, precision)."""
    from .upload import parse_transactions_csv
    from .evaluation import confusion, rates

    try:
        parsed = parse_transactions_csv(u.csv)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    events = parsed["events"]
    out = {"rows": parsed["rows"], "valid": len(events), "rejected": len(parsed["errors"]),
           "errors": parsed["errors"][:200], "warnings": parsed["warnings"]}
    if u.validate_only:
        return {**out, "scored": 0, "validate_only": True}

    eng = _engine()
    results, y_true, y_pred_stopped, y_pred_blocked = [], [], [], []
    mix = {"ALLOW": 0, "REVIEW": 0, "CHALLENGE": 0, "BLOCK": 0}
    for ev in events:
        labelled = ev.pop("_labelled", False)
        ev["_label_known"] = labelled
        case = eng.process(ev)
        mix[case["action"]] += 1
        stopped = case["action"] in ("BLOCK", "CHALLENGE")
        blocked = case["action"] == "BLOCK"
        if labelled:
            y_true.append(int(case["label"]))
            y_pred_stopped.append(int(stopped))
            y_pred_blocked.append(int(blocked))
        results.append({"id": case["id"], "ts": case["ts"], "cust_id": case["cust_id"],
                        "amount": case["amount"], "action": case["action"], "risk": case["risk"],
                        "age_bracket": case["age_bracket"], "label": case["label"] if labelled else None,
                        "reason": (case["reasons"][1] if len(case["reasons"]) > 1 else case["reasons"][0])})
        await hub.broadcast({"type": "case", "case": case})
    await hub.broadcast({"type": "metrics", "metrics": eng.metrics_snapshot(), "drift": eng.drift_status()})

    labelled_summary = None
    if y_true:
        # Keep two definitions explicit: "stopped" includes CHALLENGE and is
        # useful for friction/recall analysis; the project false-positive
        # criterion counts only genuine transactions that were actually BLOCKED.
        cm_stopped = confusion(y_true, y_pred_stopped)
        r_stopped = rates(cm_stopped)
        cm_blocked = confusion(y_true, y_pred_blocked)
        r_blocked = rates(cm_blocked)
        labelled_summary = {"labelled_rows": len(y_true),
                            "confusion_matrix": cm_stopped,
                            "recall": r_stopped["recall"],
                            "precision": r_stopped["precision"],
                            "false_positive_rate": r_blocked["false_positive_rate"],
                            "block_confusion_matrix": cm_blocked,
                            "block_recall": r_blocked["recall"],
                            "block_precision": r_blocked["precision"],
                            "stopped_false_positive_rate": r_stopped["false_positive_rate"]}
    return {**out, "scored": len(results), "decision_mix": mix,
            "stopped": mix["BLOCK"] + mix["CHALLENGE"],
            "labelled": labelled_summary, "results": results[:500]}


@app.get("/evaluation")
def evaluation_report() -> dict:
    """The held-out model evaluation report (confusion matrix, precision /
    recall / F1 / MCC with 95% CIs, ROC-AUC, PR-AUC, calibration, per-scenario
    recall, per-age fairness). Produced offline by `python -m sentinel.eval` —
    scoring a full held-out world takes ~2 minutes, too long for a request —
    and committed alongside the model so it always describes that model."""
    from .evaluation import load_report
    r = load_report()
    if r is None:
        raise HTTPException(404, "no evaluation report yet — run `python -m sentinel.eval`")
    return r


@app.get("/drift")
def drift() -> dict:
    return _engine().drift_status()


@app.get("/online/status")
def online_status() -> dict:
    """Status of the incremental online-learning correction layer (see
    model.OnlineAdjuster): how many analyst-confirmed labels it has folded
    in, and whether it is active yet (needs >=5 before it applies any
    correction)."""
    return _engine().online.status()


@app.get("/cases")
def cases(limit: int = 60, only: str | None = None) -> list[dict]:
    return _engine().recent_cases(limit=limit, only=only)


@app.get("/cases/{case_id}")
def case_by_id(case_id: int) -> dict:
    c = _engine().get_case(case_id)
    if c is None:
        raise HTTPException(404, f"case {case_id} is no longer in the live buffer")
    return c


class VerificationIn(BaseModel):
    passed: bool


@app.post("/cases/{case_id}/verification")
def case_verification(case_id: int, v: VerificationIn) -> dict:
    """Report the step-up (OTP) result for a CHALLENGEd transaction. Passed =
    the payment went through and is learned as normal for that customer;
    failed = recorded as confirmed fraud for the entity statistics."""
    try:
        return _engine().verify_challenge(case_id, v.passed)
    except KeyError:
        raise HTTPException(404, f"case {case_id} is no longer in the live buffer")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/whatif")
def whatif(w: WhatIfIn) -> dict:
    try:
        return _engine().whatif(w.case_id, dict(w.overrides))
    except KeyError:
        raise HTTPException(404, f"no case {w.case_id} in the recent buffer")


@app.get("/robustness/features")
def robustness_features() -> dict:
    """Which features the /robustness sweep can probe, for populating the
    dashboard's dropdown."""
    return {"features": _engine().sweepable_features()}


@app.get("/robustness/{case_id}")
def robustness(case_id: int, feature: str = "amount", steps: int = 21) -> dict:
    """Adversarial-robustness sweep: hold a case's other features fixed and
    vary ONE across its real-world range, showing how the risk score and
    decision respond. A single clean flip = a well-defined decision
    boundary; several flips = a jagged one an attacker could exploit by
    trial and error. This is the same /whatif re-scoring path, run as a
    sweep instead of one point, so the curve is real model output, not a
    canned demo."""
    try:
        return _engine().robustness_sweep(case_id, feature, steps=max(5, min(steps, 61)))
    except KeyError:
        raise HTTPException(404, f"no case {case_id} in the recent buffer")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/simulator/inject_ring/{scenario}")
async def sim_inject_ring(scenario: str, ring_size: int = 4) -> dict:
    """Run a fraud playbook against several customers sharing one device or
    mule beneficiary — a real fraud ring. Unlike /simulator/inject (one
    victim), this actually creates the shared entities the forensics graph
    and ring_size/fanout features are built to detect."""
    _require_simulator()
    sim: Simulator = _sim()
    try:
        result = sim.inject_ring(scenario, ring_size=ring_size)
    except KeyError:
        raise HTTPException(404, f"unknown scenario '{scenario}'; try {list(FRAUD_PLAYBOOKS)}")
    for case in result["cases"]:
        await hub.broadcast({"type": "case", "case": case})
    await hub.broadcast({"type": "metrics", "metrics": _engine().metrics_snapshot(),
                          "drift": _engine().drift_status()})
    return {k: v for k, v in result.items() if k != "cases"} | {"n_cases": len(result["cases"])}


@app.post("/qa")
def qa(q: QAIn) -> dict:
    """Ask a natural-language question about one specific scored case.
    Grounded in that case's real decision/risk/rules/feature data (see
    qa.py) via a real Groq LLM call — not a canned response, but also not
    free to invent facts outside what Sentinel actually computed."""
    from .qa import QAError, answer_question
    case = _engine().get_case(q.case_id)
    if case is None:
        raise HTTPException(404, f"no case {q.case_id} in the recent buffer")
    try:
        result = answer_question(case, q.question)
    except QAError as e:
        raise HTTPException(503, str(e))
    return {"case_id": q.case_id, "question": q.question, **result}


@app.get("/graph/{case_id}")
def graph(case_id: int) -> dict:
    """Real entity-connection graph for a case: which other customers share
    its device, beneficiary, card BIN or merchant, per Sentinel's own
    fraud-ring tracking (EntityRegistry). No synthetic or external data."""
    try:
        return _engine().graph_for_case(case_id)
    except KeyError:
        raise HTTPException(404, f"no case {case_id} in the recent buffer")


@app.get("/scenarios")
def scenarios() -> dict:
    from .datasets.synthetic import ADVERSARIAL
    return {"scenarios": list(FRAUD_PLAYBOOKS), "adversarial": sorted(ADVERSARIAL)}


@app.post("/feedback")
async def feedback(fb: FeedbackIn) -> dict:
    rec = _engine().record_feedback(
        cust_id=fb.cust_id, ts=fb.ts, amount=fb.amount,
        label=fb.label, kind=fb.kind, note=fb.note, analyst=fb.analyst)
    await hub.broadcast({"type": "queue", "queue": _engine().queue.counts()})
    return {"recorded": rec, "total": len(_engine().feedback)}


# ---------------- multi-analyst case queue ----------------
@app.get("/queue")
def queue_list(status: str | None = None) -> dict:
    """The shared review queue: every REVIEW/CHALLENGE case, who (if anyone)
    has claimed it, and its resolution once an analyst disposes of it via
    /feedback. `status` filters to open | claimed | resolved."""
    eng = _engine()
    items = eng.queue.snapshot(status=status)
    # attach the live case data (amount/merchant/risk/etc.) so the frontend
    # doesn't need a second round-trip per row
    for item in items:
        c = eng.get_case(item["case_id"])
        if c:
            item["case"] = {k: c[k] for k in
                             ("cust_id", "amount", "merchant_id", "city",
                              "country", "action", "risk", "ts", "summary")}
    return {"items": items, "counts": eng.queue.counts()}


@app.post("/queue/{case_id}/claim")
async def queue_claim(case_id: int, body: QueueClaimIn) -> dict:
    try:
        entry = _engine().queue.claim(case_id, body.analyst)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    await hub.broadcast({"type": "queue", "queue": _engine().queue.counts()})
    return entry


@app.post("/queue/{case_id}/release")
async def queue_release(case_id: int, body: QueueClaimIn) -> dict:
    try:
        entry = _engine().queue.release(case_id, body.analyst)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    await hub.broadcast({"type": "queue", "queue": _engine().queue.counts()})
    return entry


@app.post("/simulator/config")
def sim_config(cfg: SimConfigIn) -> dict:
    _require_simulator()
    sim: Simulator = _sim()
    sim.configure(rate=cfg.rate, fraud_rate=cfg.fraud_rate, running=cfg.running)
    return {"rate": sim.rate, "fraud_rate": sim.fraud_rate, "running": sim.running}


@app.post("/simulator/inject/{scenario}")
def sim_inject(scenario: str) -> dict:
    _require_simulator()
    sim: Simulator = _sim()
    try:
        return sim.inject(scenario)
    except KeyError:
        raise HTTPException(404, f"unknown scenario '{scenario}'; try {list(FRAUD_PLAYBOOKS)}")


@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket) -> None:
    if not _is_authenticated(ws.headers, ws.cookies):
        await ws.close(code=1008)                     # policy violation: not logged in
        return
    await hub.connect(ws)
    try:
        eng = STATE.get("engine")
        if eng:
            await ws.send_text(json.dumps({
                "type": "snapshot",
                "metrics": eng.metrics_snapshot(),
                "drift": eng.drift_status(),
                "cases": eng.recent_cases(limit=40),
            }, default=str))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        hub.disconnect(ws)


if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(config.FRONTEND_DIR / "index.html")
