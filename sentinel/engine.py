"""The scoring engine — the single entry point the API and the simulator share.

    raw txn ─▶ behavioural + entity/graph features ─▶ calibrated GBM ─┐
                                                     IsolationForest ─┤
              deterministic rules ───────────────────────────────────┴─▶ decision
                                                                         │
              update profile + entity registry (if not blocked) ◀────────┤
              feed drift monitor, persist snapshot every N ◀─────────────┘
"""
from __future__ import annotations

import heapq
import threading
from collections import deque
from datetime import datetime, timedelta

from . import config
from .datasets import SyntheticSource, generate_customers, age_bracket, age_for_external_id
from .decision import decide, shadow_actions
from .drift import DriftMonitor
from .entities import EntityRegistry
from .feedback import FeedbackStore
from .features import ProfileState, compute_features
from .model import FraudModel, OnlineAdjuster
from .narrate import narrate
from .queue_store import QueueStore
from .rules import evaluate_rules
from .state import SnapshotStore

# Adversarial robustness sweep: for each feature a probing attacker might try
# to tune (amount just under a threshold, spoof a familiar device, disguise
# unusual timing/velocity...), the plausible real-world range to sweep across.
# Picking a bounded, meaningful range per feature (rather than an arbitrary
# 0..1) is what makes the resulting curve a fair test of robustness instead
# of an out-of-distribution stress test that would look artificially fragile.
_SWEEP_RANGES: dict[str, tuple[float, float]] = {
    "amount": (1.0, 5000.0),
    "amount_z": (-2.0, 8.0),
    "new_device": (0.0, 1.0),
    "new_merchant": (0.0, 1.0),
    "new_beneficiary": (0.0, 1.0),
    "new_country": (0.0, 1.0),
    "is_foreign": (0.0, 1.0),
    "is_night": (0.0, 1.0),
    "txn_count_1h": (0.0, 25.0),
    "txn_count_5m": (0.0, 15.0),
    "failed_logins_1h": (0.0, 8.0),
    "merchant_fraud_rate": (0.0, 0.6),
    "device_fraud_rate": (0.0, 0.6),
    "beneficiary_fraud_rate": (0.0, 0.6),
    "ring_size": (0.0, 8.0),
    "speed_kmh": (0.0, 2000.0),
}


def _default_range(current: float) -> tuple[float, float]:
    """Fallback sweep range for a feature with no curated entry: a wide
    symmetric band around its current value (or 0..1 if it looks like a
    flag)."""
    if 0.0 <= current <= 1.0:
        return (0.0, 1.0)
    span = max(abs(current), 1.0) * 3
    return (max(0.0, current - span), current + span)


class Metrics:
    def __init__(self) -> None:
        self.processed = 0
        self.by_action = {"ALLOW": 0, "REVIEW": 0, "CHALLENGE": 0, "BLOCK": 0}
        self.fraud_total = 0
        self.fraud_stopped = 0
        self.fraud_missed = 0
        self.false_positives = 0
        self.amount_saved = 0.0
        self.by_scenario: dict[str, dict] = {}
        # live A/B of the three policies: rules-only, model-only, both
        self.policy = {p: {"caught": 0, "fp": 0} for p in ("rules_only", "model_only", "full")}

    def observe(self, action: str, label: int, amount: float, scenario: str,
                shadows: dict | None = None) -> None:
        self.processed += 1
        self.by_action[action] += 1
        stopped = action in ("BLOCK", "CHALLENGE")
        if label == 1:
            self.fraud_total += 1
            self.fraud_stopped += stopped
            self.fraud_missed += not stopped
            if stopped:
                self.amount_saved += amount
            s = self.by_scenario.setdefault(scenario, {"total": 0, "stopped": 0})
            s["total"] += 1
            s["stopped"] += int(stopped)
        elif action == "BLOCK":
            self.false_positives += 1
        for name, act in (shadows or {}).items():
            hit = act in ("BLOCK", "CHALLENGE")
            if label == 1 and hit:
                self.policy[name]["caught"] += 1
            elif label == 0 and act == "BLOCK":
                self.policy[name]["fp"] += 1

    def snapshot(self) -> dict:
        legit = self.processed - self.fraud_total
        return {
            "processed": self.processed,
            "by_action": dict(self.by_action),
            "fraud_total": self.fraud_total,
            "fraud_stopped": self.fraud_stopped,
            "fraud_missed": self.fraud_missed,
            "false_positives": self.false_positives,
            "detection_rate": round(self.fraud_stopped / self.fraud_total, 4) if self.fraud_total else 0.0,
            "false_positive_rate": round(self.false_positives / legit, 5) if legit else 0.0,
            "amount_saved": round(self.amount_saved, 2),
            "by_scenario": {k: {**v, "rate": round(v["stopped"] / v["total"], 3)}
                            for k, v in sorted(self.by_scenario.items())},
            "policy_comparison": {
                p: {
                    "detection_rate": round(v["caught"] / self.fraud_total, 4) if self.fraud_total else 0.0,
                    "false_positive_rate": round(v["fp"] / legit, 5) if legit else 0.0,
                    "caught": v["caught"], "fp": v["fp"],
                } for p, v in self.policy.items()
            },
        }


class Engine:
    def __init__(self, model: FraudModel, autosnapshot: bool = True,
                 label_delay_hours: float | None = None) -> None:
        self.model = model
        self.autosnapshot = autosnapshot
        # How long after a transaction its fraud outcome becomes known to the
        # entity statistics (device / payee / merchant / card fraud rates).
        # A bank learns outcomes late — chargebacks, customer reports, analyst
        # decisions — so by default a transaction's own label is NOT fed into
        # those statistics when it is scored; confirmed fraud lands only after
        # this delay. 0 = legacy "outcome known instantly" (an upper bound,
        # useful only for comparison); 0 selects it.
        hours = config.LABEL_DELAY_HOURS if label_delay_hours is None else label_delay_hours
        self.label_delay = timedelta(hours=hours) if hours > 0 else None
        self._pending_labels: list = []     # heap of (release_ts, seq, entity keys)
        self._pending_challenges: dict = {}  # case id -> txn awaiting its verification outcome
        self.profiles: dict[str, ProfileState] = {}
        self.customers: dict = {}
        self.entities = EntityRegistry()
        self.metrics = Metrics()
        self.drift = DriftMonitor(model.meta.get("ref_scores", []))
        self.feedback = FeedbackStore()
        self.online = OnlineAdjuster()  # incremental correction from analyst feedback (see model.py)
        self.queue = QueueStore()  # shared multi-analyst review queue (see queue_store.py)
        self.store = SnapshotStore()
        self.cases: deque = deque(maxlen=config.CASE_BUFFER)
        self._seq = 0
        self._lock = threading.RLock()

    # ---- population / warm-up --------------------------------------
    def seed_population(self) -> None:
        # use the *same* customer population the model was trained on, so live
        # traffic statistics match the drift reference (in production, your live
        # customers are your training population)
        roster = generate_customers(config.TRAIN_CUSTOMERS, config.TRAIN_SEED)
        self.customers = {c.cust_id: c for c in roster}

        if self._restore_snapshot():
            print(f"[sentinel] restored warm state from {self.store.backend} "
                  f"({len(self.profiles)} profiles, seq={self._seq})")
            return

        start = datetime.utcnow().replace(microsecond=0)
        hist = SyntheticSource(config.TRAIN_CUSTOMERS, config.SEED_HISTORY_DAYS,
                               seed=config.TRAIN_SEED, inject_fraud=False).events()
        # slide the fixed 2025-01-01 origin so history ends ~now; apply the SAME
        # shift to account_open so account-age features match the training range
        shift = start - timedelta(days=config.SEED_HISTORY_DAYS) - hist[0]["ts"]
        for c in roster:
            self.profiles[c.cust_id] = ProfileState(
                c.cust_id, c.home_country, c.home_lat, c.home_lon, c.account_open + shift)

        for ev in hist:
            ev["ts"] = ev["ts"] + shift
            ps = self.profiles[ev["cust_id"]]
            if ev["type"] == "login":
                ps.add_login(ev["ts"], ev["success"], ev.get("device_id", ""))
            else:
                self.entities.observe(ev, 0, ev["ts"])
                ps.update(ev)
        print(f"[sentinel] warmed {len(self.profiles)} behavioural profiles "
              f"from {len(hist):,} historical events")
        self._calibrate_drift_reference()

    def _calibrate_drift_reference(self, n: int = 1500) -> None:
        """Set the drift reference from *this deployment's* own warmed‑up
        ambient distribution — not the training set. In production you'd
        baseline PSI on a window of real post‑go‑live traffic; this is the
        synthetic equivalent, and it makes PSI read ~0 until traffic genuinely
        shifts."""
        import random as _r

        from .datasets import sample_legit_txn
        rng = _r.Random(config.TRAIN_SEED + 4242)
        sim_now = datetime.utcnow()
        risks = []
        for _ in range(n):
            sim_now += timedelta(seconds=95)
            cust = self.customers[rng.choice(list(self.customers))]
            txn = sample_legit_txn(cust, sim_now, rng)
            ps = self.profiles[txn["cust_id"]]
            feat = compute_features(txn, ps, self.entities, sim_now)
            risks.append(self.model.score(feat).risk)
        from .drift import score_histogram
        self.drift.reference = score_histogram(risks)
        print(f"[sentinel] drift reference calibrated on {n} synthetic ambient scores "
              f"(re-baselined after {config.DRIFT_BASELINE_AFTER} live transactions)")

    # ---- snapshot persistence -----------------------------------
    def _state_blob(self) -> dict:
        return {"profiles": self.profiles, "entities": self.entities,
                "metrics": self.metrics, "seq": self._seq,
                "pending_labels": list(getattr(self, "_pending_labels", []))}

    def _restore_snapshot(self) -> bool:
        blob = self.store.load()
        if not blob:
            return False
        self.profiles = blob["profiles"]
        self.entities = blob["entities"]
        self.metrics = blob["metrics"]
        self._seq = blob["seq"]
        self._pending_labels = list(blob.get("pending_labels", []))
        heapq.heapify(self._pending_labels)
        return True

    def persist(self) -> None:
        with self._lock:
            self.store.save(self._state_blob())

    def _release_labels(self, now: datetime) -> None:
        """Deliver fraud outcomes whose delay has elapsed to the entity stats."""
        pend = getattr(self, "_pending_labels", None)
        while pend and pend[0][0] <= now:
            release_ts, _seq, keys = heapq.heappop(pend)
            self.entities.confirm_fraud(keys, release_ts)

    # ---- feedback ------------------------------------------------
    def record_feedback(self, *, cust_id, ts, amount, label, kind="disposition",
                         note="", analyst: str | None = None):
        rec = self.feedback.record(cust_id=cust_id, ts=ts, amount=amount,
                                   label=label, kind=kind, note=note)
        if label == 1:
            self.entities.confirm_fraud(
                {"cust_id": cust_id, "merchant_id": note or "?", "card_bin": "?",
                 "device_id": "?", "beneficiary": ""},
                ts if hasattr(ts, "isoformat") else datetime.utcnow())

        # Live online-learning update (see model.OnlineAdjuster): if this
        # feedback matches a transaction still in the recent buffer, its
        # features feed one partial_fit step immediately — the next
        # transaction scored with similar features gets a real, bounded
        # correction. This is the actual, honest "learns from feedback"
        # mechanism; it does not retrain the base ensemble.
        ts_dt = ts if hasattr(ts, "isoformat") else datetime.utcnow()
        for case in reversed(self.cases):
            if (case["cust_id"] == cust_id
                    and abs(case["amount"] - round(float(amount), 2)) < 0.01
                    and abs((datetime.fromisoformat(case["ts"]) - ts_dt).total_seconds()) < 2.0):
                self.online.update(case["features"], int(label))
                rec = dict(rec, online_status=self.online.status())
                # If this case was sitting in the shared review queue, this
                # disposition IS its resolution — reflect that so other
                # analysts see it leave the queue instead of double-working it.
                q = self.queue.resolve(
                    case["id"], analyst or "unassigned",
                    "fraud" if label else "legitimate")
                if q:
                    rec = dict(rec, queue_status=q)
                break
        return rec

    # ---- the pipeline ------------------------------------------
    def process(self, txn: dict) -> dict:
        with self._lock:
            now = txn.get("ts") or datetime.utcnow()
            if isinstance(now, str):
                now = datetime.fromisoformat(now)
            txn["ts"] = now
            txn.setdefault("beneficiary", "")
            txn.setdefault("card_bin", "?")

            self._release_labels(now)
            cid = txn["cust_id"]
            ps = self.profiles.get(cid)
            if ps is None:
                ps = self.profiles[cid] = ProfileState(
                    cid, txn["country"], float(txn["lat"]), float(txn["lon"]), now)

            # Age/age-bracket: the synthetic generator already stamps every
            # event it produces with cust_age/age_bracket (see
            # datasets/synthetic.py::_mk_txn). Real-data feeds (upi/paysim via
            # csv_adapter.py, and anything replayed through /replay or
            # /replay/blended) have no source age column, so txn won't carry
            # these keys — fall back to the known Customer record if this
            # cust_id has one (synthetic roster), else to a deterministic
            # hash-of-id age so the same real cust_id always reports the same
            # bracket across repeated calls instead of a fresh random value
            # or a crash on a missing field.
            if "cust_age" in txn:
                cust_age = int(txn["cust_age"])
            else:
                known = self.customers.get(cid)
                cust_age = known.age if known is not None else age_for_external_id(cid)
            cust_age_bracket = txn.get("age_bracket") or age_bracket(cust_age)

            # a late analyst/chargeback verdict overrides the demo label
            fb = self.feedback.label_for(cid, now, txn["amount"])
            label = fb if fb is not None else int(txn.get("label", 0))

            feat = compute_features(txn, ps, self.entities, now)
            score = self.model.score(feat)
            online_adj = self.online.adjustment(feat)
            if online_adj:
                score.risk = float(max(0.0, min(1.0, score.risk + online_adj)))
            hits = evaluate_rules(feat, txn, ps.velocity_baseline(now))
            # Explain only cases an analyst might actually review.
            from .config import RISK_REVIEW
            if hits or score.risk >= RISK_REVIEW or score.fraud_proba >= self.model.label_threshold:
                score.top_features = self.model.explain_features(feat)
                score.counterfactual = self.model.find_counterfactual(feat)
            decision = decide(score, hits, txn, self.model.label_threshold)
            shadows = shadow_actions(score, hits, self.model.label_threshold)

            self.metrics.observe(decision.action, label, float(txn["amount"]),
                                 txn.get("scenario", "fraud" if label else "legit"),
                                 shadows=shadows)
            self.drift.observe(score.risk, allowed=decision.action == "ALLOW")

            # The customer's behavioural profile learns only from transactions
            # that actually went through (ALLOW, or REVIEW = allowed and queued
            # for an analyst). A CHALLENGE is paused pending verification — an
            # account-takeover attacker typically can't pass it — so treating
            # it as "normal" let the first stopped attempt teach the profile
            # that big transfers to the mule are routine, and the attacker's
            # follow-up transfers then sailed through. Training never lets
            # fraud update a profile (train.replay), so this also removes a
            # train/serve mismatch.
            #
            # A CHALLENGE's outcome is the step-up verification (OTP): a genuine
            # customer passes and the payment goes through — it must then be
            # learned, or a busy new customer who gets challenged once has a
            # frozen profile and is challenged forever. Where the true outcome
            # is known (simulator, dataset replays, evaluation, labelled
            # uploads) verification is resolved from it: genuine passes,
            # fraud fails (optimistic about attackers — some pass via SIM
            # swap). Otherwise (live /score) it stays pending until the bank
            # reports it via verify_challenge() / POST /cases/{id}/verification.
            outcome_known = txn.pop("_label_known", True)
            verification = None
            if decision.action in ("ALLOW", "REVIEW"):
                ps.update(txn)
            elif decision.action == "CHALLENGE":
                if outcome_known:
                    verification = "simulated_pass" if label == 0 else "simulated_fail"
                    if label == 0:
                        ps.update(txn)
                else:
                    verification = "pending"
                    self._pending_challenges[self._seq + 1] = dict(txn)
                    while len(self._pending_challenges) > config.CASE_BUFFER:
                        self._pending_challenges.pop(next(iter(self._pending_challenges)))
            # Entity/graph tracking (device, beneficiary, merchant, BIN fraud
            # history and fraud-ring fan-in/out) always records, even on
            # BLOCK: a blocked fraud attempt is exactly the signal that should
            # mark a device or mule account as compromised for the *next*
            # transaction that touches it.
            if self.label_delay is None:
                self.entities.observe(txn, label, now)
            else:
                self.entities.observe(txn, 0, now)
                if label == 1:
                    keys = {k: txn.get(k, "") for k in
                            ("cust_id", "merchant_id", "card_bin", "device_id", "beneficiary")}
                    heapq.heappush(self._pending_labels, (now + self.label_delay, self._seq, keys))

            self._seq += 1
            if self.autosnapshot and self._seq % config.SNAPSHOT_EVERY == 0:
                try:
                    self.entities.prune(now)
                    self.store.save(self._state_blob())
                except Exception:
                    pass

            case = {
                "id": self._seq, "ts": now.isoformat(),
                "epoch_ms": int(now.timestamp() * 1000),
                "cust_id": cid, "amount": round(float(txn["amount"]), 2),
                "cust_age": cust_age, "age_bracket": cust_age_bracket,
                "mcc": txn["mcc"], "channel": txn["channel"],
                "merchant_id": txn["merchant_id"], "beneficiary": txn.get("beneficiary", ""),
                "device_id": txn.get("device_id", "?"), "card_bin": txn.get("card_bin", "?"),
                "city": txn.get("city", ""), "country": txn["country"],
                "label": label, "scenario": txn.get("scenario", "fraud" if label else "legit"),
                "action": decision.action, "shadows": shadows, "risk": decision.risk,
                "fraud_proba": round(score.fraud_proba, 4), "anomaly": round(score.anomaly, 4),
                "reasons": decision.reasons, "rule_hits": decision.rule_hits,
                "customer_alert": decision.customer_alert,
                "verification": verification,
                "explanation": [{"feature": n, "value": round(v, 3), "contribution": round(c, 4)}
                                for n, v, c in score.top_features],
                "explanation_method": "grouped counterfactual reference search",
                "counterfactual": score.counterfactual,
                "features": {k: round(v, 3) for k, v in feat.items()},
                "online_adjustment": round(online_adj, 4),
                "correct": (decision.action in ("BLOCK", "CHALLENGE")) == bool(label),
            }
            case["summary"] = narrate(case)
            self.cases.append(case)
            if decision.action in ("REVIEW", "CHALLENGE"):
                self.queue.enqueue(case["id"], action=decision.action,
                                   risk=decision.risk, cust_id=cid,
                                   amount=case["amount"])
            return case

    # ---- exposed to the API --------------------------------------
    def recent_cases(self, limit=60, only=None):
        items = list(self.cases)[::-1]
        if only == "alerts":
            items = [c for c in items if c["action"] in ("BLOCK", "CHALLENGE", "REVIEW")]
        return items[:limit]

    def _baseline_for(self, case: dict) -> dict | None:
        """The customer's velocity baseline as of a stored case, so what-if and
        robustness re-scoring apply the same velocity rules as the live call."""
        ps = self.profiles.get(case.get("cust_id"))
        if ps is None:
            return None
        ts = case.get("ts")
        now = datetime.fromisoformat(ts) if isinstance(ts, str) else (ts or datetime.utcnow())
        return ps.velocity_baseline(now)

    def verify_challenge(self, case_id: int, passed: bool) -> dict:
        """The bank's step-up verification result for a CHALLENGEd case. A
        pass means the payment went through, so it becomes part of the
        customer's normal behaviour; a fail is recorded as confirmed fraud."""
        with self._lock:
            pend = getattr(self, "_pending_challenges", {})
            txn = pend.pop(case_id, None)
            case = self.get_case(case_id)
            if txn is None:
                if case is None:
                    raise KeyError(case_id)
                raise ValueError("this case is not awaiting verification "
                                 f"(action {case['action']}, or already resolved)")
            if passed:
                ps = self.profiles.get(txn["cust_id"])
                if ps is not None:
                    ps.update(txn)
            else:
                self.entities.confirm_fraud(txn, txn["ts"])
            if case is not None:
                case["verification"] = "passed" if passed else "failed"
            return {"case_id": case_id, "verification": "passed" if passed else "failed"}

    def get_case(self, case_id: int):
        return next((c for c in self.cases if c["id"] == case_id), None)

    def graph_for_case(self, case_id: int) -> dict:
        """Real entity connections for a case — which other customers share
        this transaction's device, beneficiary, card BIN or merchant. Backs
        the dashboard's forensics graph. Every edge comes from transactions
        Sentinel has actually processed (via EntityRegistry), not synthetic
        or external data."""
        from datetime import datetime as _dt

        case = self.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        txn = {
            "cust_id": case["cust_id"], "merchant_id": case["merchant_id"],
            "beneficiary": case.get("beneficiary", ""),
            "device_id": case.get("device_id", "?"), "card_bin": case.get("card_bin", "?"),
        }
        now = _dt.fromisoformat(case["ts"]) if isinstance(case["ts"], str) else case["ts"]
        conn = self.entities.connections(txn, now)

        nodes = [{"id": conn["cust_id"], "label": conn["cust_id"], "kind": "focus_customer"}]
        edges = []
        seen = {conn["cust_id"]}
        for link in conn["links"]:
            entity_id = f'{link["kind"]}:{link["key"]}'
            nodes.append({
                "id": entity_id, "label": link["key"], "kind": f'entity_{link["kind"]}',
                "fraud_rate": link["fraud_rate"], "total_customers": link["total_customers"],
            })
            edges.append({"source": conn["cust_id"], "target": entity_id, "kind": link["kind"]})
            for other in link["shared_with"]:
                if other not in seen:
                    nodes.append({"id": other, "label": other, "kind": "linked_customer"})
                    seen.add(other)
                edges.append({"source": other, "target": entity_id, "kind": link["kind"]})
        return {"case_id": case_id, "nodes": nodes, "edges": edges, "raw": conn}

    def whatif(self, case_id: int, overrides: dict) -> dict:
        """Re-score a past case with feature overrides — the interactive
        counterfactual explorer behind the dashboard 'What-if' panel."""
        from .decision import decide as _decide
        from .rules import evaluate_rules as _rules

        base = self.get_case(case_id)
        if base is None:
            raise KeyError(case_id)
        feat = dict(base["features"])

        mult = float(overrides.pop("amount_mult", 1.0) or 1.0)
        if abs(mult - 1.0) > 1e-6:
            import math as _m
            new_amt = max(feat["amount"] * mult, 0.01)
            feat["amount"] = new_amt
            feat["amount_log"] = _m.log1p(new_amt)
            feat["amount_z"] *= mult
            feat["amount_to_max"] *= mult
            feat["amt_sum_1h"] *= mult
            feat["amt_sum_24h"] *= mult
            feat["high_risk_mcc"] = feat.get("high_risk_mcc", 0.0)
        for k, v in overrides.items():
            if k in feat:
                feat[k] = float(v)

        score = self.model.score(feat, explain=True)
        txn = {"mcc": base["mcc"], "channel": base["channel"], "country": base["country"],
               "amount": feat["amount"], "merchant_id": base["merchant_id"],
               "beneficiary": base.get("beneficiary", "")}
        hits = _rules(feat, txn, self._baseline_for(base))
        dec = _decide(score, hits, txn, self.model.label_threshold)
        preview = {**base, "action": dec.action, "risk": dec.risk,
                   "fraud_proba": round(score.fraud_proba, 4),
                   "anomaly": round(score.anomaly, 4), "rule_hits": dec.rule_hits,
                   "reasons": dec.reasons,
                   "explanation": [{"feature": n, "value": round(v, 3), "contribution": round(c, 4)}
                                   for n, v, c in score.top_features],
                   "features": {k: round(v, 3) for k, v in feat.items()}}
        return {
            "base": {"action": base["action"], "risk": base["risk"],
                     "fraud_proba": base["fraud_proba"]},
            "whatif": {"action": dec.action, "risk": round(dec.risk, 4),
                       "fraud_proba": round(score.fraud_proba, 4),
                       "anomaly": round(score.anomaly, 4),
                       "rule_hits": dec.rule_hits},
            "summary": narrate(preview),
            "flipped": dec.action != base["action"],
        }

    # ---- adversarial robustness ----------------------------------
    def robustness_sweep(self, case_id: int, feature: str, steps: int = 21) -> dict:
        """How the risk score and decision respond as ONE feature is swept
        across its plausible range, holding everything else fixed — the
        same re-scoring path as /whatif, run repeatedly. This is what an
        adversary probing for a blind spot would effectively be doing by
        trial and error; visualizing the curve shows directly whether the
        decision boundary is a sharp cliff (fragile / gameable right at the
        edge) or a smooth ramp (harder to slip just under), and whether the
        rules layer catches what the ML score alone would miss."""
        from .decision import decide as _decide
        from .rules import evaluate_rules as _rules

        base = self.get_case(case_id)
        if base is None:
            raise KeyError(case_id)
        base_feat = dict(base["features"])
        if feature not in base_feat:
            raise ValueError(f"unknown feature '{feature}'")

        lo, hi = _SWEEP_RANGES.get(feature, _default_range(base_feat[feature]))
        points = []
        for i in range(steps):
            v = lo + (hi - lo) * i / max(steps - 1, 1)
            feat = dict(base_feat)
            feat[feature] = v
            if feature == "amount":
                # mirror /whatif's amount_mult handling so the swept amount
                # drags its derived features along realistically, instead of
                # looking artificially risk-free at every value
                import math as _m
                base_amt = max(base_feat["amount"], 0.01)
                mult = max(v, 0.01) / base_amt
                feat["amount_log"] = _m.log1p(max(v, 0.0))
                feat["amount_z"] = base_feat.get("amount_z", 0.0) * mult
                feat["amount_to_max"] = base_feat.get("amount_to_max", 0.0) * mult
                feat["amt_sum_1h"] = base_feat.get("amt_sum_1h", 0.0) * mult
                feat["amt_sum_24h"] = base_feat.get("amt_sum_24h", 0.0) * mult
            score = self.model.score(feat)
            txn = {"mcc": base["mcc"], "channel": base["channel"], "country": base["country"],
                   "amount": feat.get("amount", base["amount"]), "merchant_id": base["merchant_id"],
                   "beneficiary": base.get("beneficiary", "")}
            hits = _rules(feat, txn, self._baseline_for(base))
            dec = _decide(score, hits, txn, self.model.label_threshold)
            points.append({"value": round(v, 4), "risk": round(dec.risk, 4),
                           "action": dec.action, "rule_hits": len(dec.rule_hits)})

        actions = [p["action"] for p in points]
        flips = sum(1 for a, b in zip(actions, actions[1:]) if a != b)
        return {
            "case_id": case_id, "feature": feature,
            "base_value": round(base_feat[feature], 4),
            "base_action": base["action"],
            "range": [lo, hi], "points": points,
            "n_decision_flips": flips,
            "monotonic": flips <= 1,  # a single flip = a clean threshold; >1 = a jagged, gameable boundary
        }

    def sweepable_features(self) -> list[str]:
        return list(_SWEEP_RANGES)

    def metrics_snapshot(self):
        return self.metrics.snapshot()

    def age_breakdown(self) -> dict:
        """Per-age-cohort decision and outcome stats over the cases currently
        in the buffer.

        Two distinct uses, which is why both counts and rates are returned:

        * *Operational* — where the fraud actually is. Fraud typologies differ
          sharply by cohort (elder-targeted social engineering vs. young-adult
          account takeover), so a fraud-ops lead wants the caught/missed split
          per bracket, not one global number.
        * *Fairness* — whether Sentinel treats cohorts differently. A model
          that blocks a far larger share of over-60s' legitimate spend is a
          disparate-impact problem a bank's model-risk function has to see,
          even when overall accuracy looks fine. `false_positive_rate` (legit
          transactions stopped, per bracket) is the number that surfaces it.

        Rates are None, not 0.0, when a bracket has no denominator yet — an
        empty cohort must not read as a perfect score.
        """
        from .datasets import AGE_BRACKETS

        order = [label for label, _lo, _hi in AGE_BRACKETS]
        stats = {b: {"bracket": b, "transactions": 0, "fraud": 0, "legit": 0,
                     "stopped": 0, "fraud_caught": 0, "legit_stopped": 0,
                     "amount": 0.0, "amount_stopped": 0.0} for b in order}

        with self._lock:
            cases = list(self.cases)

        for c in cases:
            b = c.get("age_bracket")
            if b not in stats:
                continue
            s = stats[b]
            stopped = c.get("action") in ("BLOCK", "CHALLENGE")
            is_fraud = bool(c.get("label"))
            amt = float(c.get("amount", 0.0))
            s["transactions"] += 1
            s["amount"] += amt
            s["fraud" if is_fraud else "legit"] += 1
            if stopped:
                s["stopped"] += 1
                s["amount_stopped"] += amt
                s["fraud_caught" if is_fraud else "legit_stopped"] += 1

        out = []
        for b in order:
            s = stats[b]
            s["amount"] = round(s["amount"], 2)
            s["amount_stopped"] = round(s["amount_stopped"], 2)
            # detection_rate: share of this cohort's fraud that was stopped
            s["detection_rate"] = (s["fraud_caught"] / s["fraud"]) if s["fraud"] else None
            # false_positive_rate: share of this cohort's LEGIT spend stopped —
            # the fairness-relevant number
            s["false_positive_rate"] = (s["legit_stopped"] / s["legit"]) if s["legit"] else None
            s["fraud_rate"] = (s["fraud"] / s["transactions"]) if s["transactions"] else None
            s["avg_amount"] = round(s["amount"] / s["transactions"], 2) if s["transactions"] else None
            out.append(s)

        scored = sum(s["transactions"] for s in out)
        fprs = [s["false_positive_rate"] for s in out if s["false_positive_rate"] is not None]
        return {
            "brackets": out,
            "total_scored": scored,
            # A simple, honest fairness headline: the spread between the most-
            # and least-affected cohort's false-positive rate. Only meaningful
            # once at least two cohorts have legitimate traffic.
            "fpr_gap": round(max(fprs) - min(fprs), 4) if len(fprs) >= 2 else None,
            "note": ("Cohort stats over the last "
                     f"{scored} scored transactions in the live buffer."),
        }

    def drift_status(self):
        return self.drift.status()

    def sample_customer_id(self, rng):
        return rng.choice(list(self.customers))

