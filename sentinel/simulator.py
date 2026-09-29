"""Live traffic generator for the demo.

A **simulated clock** advances much faster than wall‑clock time, so each
customer's transaction cadence (and every velocity / recency feature) stays
realistic even though the dashboard shows dozens of transactions per real second.

Two drive modes:
  * **task** (default) — an asyncio loop emits ambient traffic and plays injected
    attacks over ~11 s so you can watch them.
  * **serverless** (`SENTINEL_SERVERLESS=1`) — no background task; the client
    calls `POST /tick` for ambient traffic and injections are processed
    synchronously. Lets the app run on request‑scoped hosts (Vercel functions).
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from . import config
from .datasets import FRAUD_PLAYBOOKS, sample_legit_txn
from .engine import Engine

_SIM_SECONDS_PER_TICK = 95.0
_PLAYBACK_SECONDS = 11.0


class Simulator:
    def __init__(self, engine: Engine, broadcast) -> None:
        self.engine = engine
        self.broadcast = broadcast
        self.rng = random.Random(config.RUNTIME_SEED + 99)
        self.rate = config.AMBIENT_TXNS_PER_SEC
        self.fraud_rate = config.AMBIENT_FRAUD_RATE
        self.running = True
        self.serverless = config.SERVERLESS
        self.sim_now = datetime.utcnow()
        self._inject: list[dict] = []
        self._task: asyncio.Task | None = None

    # ---- lifecycle -----------------------------------------------
    def start(self) -> None:
        if self.serverless:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return            # no running loop yet — the startup event will retry
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()

    def configure(self, rate=None, fraud_rate=None, running=None) -> None:
        if rate is not None:
            self.rate = max(0.2, min(rate, 40.0))
        if fraud_rate is not None:
            self.fraud_rate = max(0.0, min(fraud_rate, 0.5))
        if running is not None:
            self.running = running

    # ---- ambient traffic ---------------------------------------
    def _advance_clock(self) -> None:
        self.sim_now += timedelta(seconds=max(5.0, self.rng.gauss(
            _SIM_SECONDS_PER_TICK, _SIM_SECONDS_PER_TICK * 0.4)))

    def _one_ambient(self) -> dict:
        cust = self.engine.customers[self.engine.sample_customer_id(self.rng)]
        if self.rng.random() < self.fraud_rate:
            scen = self.rng.choice(list(FRAUD_PLAYBOOKS))
            ev = [x for x in FRAUD_PLAYBOOKS[scen](cust, self.sim_now, self.rng)
                  if x["type"] == "txn"][:1]
            if ev:
                ev[0]["ts"] = self.sim_now
                ev[0]["scenario"] = scen + " (drizzle)"
                return ev[0]
        return sample_legit_txn(cust, self.sim_now, self.rng)

    def tick(self, n: int = 10) -> list[dict]:
        """Generate and score `n` ambient transactions right now. Used by the
        serverless / polling client."""
        out = []
        due = self._drain_injects(datetime.utcnow() + timedelta(hours=1))  # all
        for ev in due:
            if ev["type"] == "login":
                ps = self.engine.profiles.get(ev["cust_id"])
                if ps:
                    ps.add_login(ev["ts"], ev["success"], ev.get("device_id", ""))
                continue
            out.append(self.engine.process(ev))
        for _ in range(max(1, min(n, 60))):
            self._advance_clock()
            out.append(self.engine.process(self._one_ambient()))
        return out

    # ---- attack injection --------------------------------------
    def _queue_inject(self, scenario: str):
        cust_id = self.engine.sample_customer_id(self.rng)
        cust = self.engine.customers[cust_id]
        events = FRAUD_PLAYBOOKS[scenario](cust, self.sim_now, self.rng)
        events.sort(key=lambda e: e["ts"])
        base_sim, t0 = self.sim_now, events[0]["ts"]
        span = (events[-1]["ts"] - t0).total_seconds() or 1.0
        scale = min(1.0, (6 * 3600.0) / span)
        real_now, n = datetime.utcnow(), len(events)
        for i, e in enumerate(events):
            e["scenario"] = scenario
            self._inject.append({
                "play_at": real_now + timedelta(seconds=1.0 + i * _PLAYBACK_SECONDS / n),
                "sim_ts": base_sim + timedelta(seconds=(e["ts"] - t0).total_seconds() * scale),
                "ev": e,
            })
        return {"scenario": scenario, "cust_id": cust_id,
                "victim_city": cust.home_city, "events": n}

    def _drain_injects(self, real_now: datetime) -> list[dict]:
        due = [q for q in self._inject if q["play_at"] <= real_now]
        self._inject = [q for q in self._inject if q["play_at"] > real_now]
        for q in due:
            q["ev"]["ts"] = q["sim_ts"]
        return [q["ev"] for q in due]

    def inject_ring(self, scenario: str, ring_size: int = 4) -> dict:
        """Run a fraud playbook against several DIFFERENT customers who share
        one device or one mule beneficiary — a real fraud ring, not just a
        single victim. The playbooks already accept `ring_device` /
        `ring_beneficiary`; the simulator just never passed one in. This is
        what actually populates EntityRegistry's device_customer_fanout /
        beneficiary_customer_fanin / ring_size — and the forensics graph."""
        if scenario not in FRAUD_PLAYBOOKS:
            raise KeyError(scenario)
        ring_size = max(2, min(ring_size, 10))
        ring_device = f"dev-ring-{self.rng.randint(1000, 9999)}"
        ring_beneficiary = f"mule_ring_{self.rng.randint(0, 99999)}"

        cust_ids = self.rng.sample(list(self.engine.customers), k=min(ring_size, len(self.engine.customers)))
        real_now, cases = datetime.utcnow(), []
        for i, cid in enumerate(cust_ids):
            cust = self.engine.customers[cid]
            events = FRAUD_PLAYBOOKS[scenario](
                cust, self.sim_now, self.rng,
                ring_device=ring_device, ring_beneficiary=ring_beneficiary)
            events.sort(key=lambda e: e["ts"])
            t0 = events[0]["ts"]
            for e in events:
                e["scenario"] = f"{scenario} (ring)"
                e["ts"] = self.sim_now + timedelta(seconds=(e["ts"] - t0).total_seconds())
                if e["type"] == "login":
                    ps = self.engine.profiles.get(e["cust_id"])
                    if ps:
                        ps.add_login(e["ts"], e["success"], e.get("device_id", ""))
                    continue
                cases.append(self.engine.process(e))
        return {
            "scenario": scenario, "ring_size": len(cust_ids), "cust_ids": cust_ids,
            "ring_device": ring_device, "ring_beneficiary": ring_beneficiary,
            "cases": cases,
        }

    def inject(self, scenario: str) -> dict:
        if scenario not in FRAUD_PLAYBOOKS:
            raise KeyError(scenario)
        meta = self._queue_inject(scenario)
        if self.serverless:                       # process the whole episode now
            cases = []
            for ev in self._drain_injects(datetime.utcnow() + timedelta(hours=1)):
                if ev["type"] == "login":
                    ps = self.engine.profiles.get(ev["cust_id"])
                    if ps:
                        ps.add_login(ev["ts"], ev["success"], ev.get("device_id", ""))
                    continue
                cases.append(self.engine.process(ev))
            meta["cases"] = cases
        return meta

    # ---- background loop -------------------------------------
    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0 / max(self.rate, 0.2))
                for ev in self._drain_injects(datetime.utcnow()):
                    if ev["type"] == "login":
                        ps = self.engine.profiles.get(ev["cust_id"])
                        if ps:
                            ps.add_login(ev["ts"], ev["success"], ev.get("device_id", ""))
                        continue
                    await self._emit(ev)
                if not self.running:
                    continue
                self._advance_clock()
                await self._emit(self._one_ambient())
        except asyncio.CancelledError:
            pass

    async def _emit(self, txn: dict) -> None:
        case = self.engine.process(txn)
        await self.broadcast({"type": "case", "case": case})
        if case["id"] % 5 == 0:
            await self.broadcast({"type": "metrics",
                                  "metrics": self.engine.metrics_snapshot(),
                                  "drift": self.engine.drift_status()})
