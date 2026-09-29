"""Analyst feedback + delayed-label handling.

Real fraud labels arrive late (a chargeback lands weeks after the transaction).
This store captures two kinds of signal:

    * ``disposition`` — an analyst reviewed a flagged case now: fraud / legit
    * ``chargeback``  — a confirmed-fraud label that arrived later

`merge_labels()` folds them back into a training event stream, and
`sample_weights()` upweights confirmed fraud and *down*weights very recent
transactions whose label may still be in flux (label maturity).

Two backends, same pattern as state.SnapshotStore / queue_store.QueueStore:
  * local disk (default) — an append-only JSONL file, fine for a single
    long-lived process (e.g. `uvicorn` run locally or on a normal VM/container
    host).
  * Redis (SENTINEL_REDIS_URL set) — required on a serverless host like
    Vercel, where every request can hit a *different* instance with its own
    empty filesystem and no shared memory. Without Redis there, analyst
    feedback would appear to vanish between requests.

Because a serverless instance is not guaranteed to survive between requests,
every mutating/reading method here re-reads the full table from the backing
store before acting, rather than trusting an in-memory `_by_key` cache built
once at `__init__` time the way a long-lived process safely could.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from threading import RLock

import numpy as np

from .config import FEEDBACK_PATH, REDIS_URL

_REDIS_KEY = "sentinel:feedback:v1"


def _key(cust_id: str, ts, amount: float) -> str:
    t = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    return f"{cust_id}|{t}|{round(float(amount), 2)}"


class FeedbackStore:
    def __init__(self, path: Path = FEEDBACK_PATH, redis_url: str = REDIS_URL):
        self.path = Path(path)
        self._lock = RLock()
        self._redis = None
        if redis_url:
            try:
                import redis  # type: ignore
                self._redis = redis.Redis.from_url(redis_url)
                self._redis.ping()
            except Exception as exc:  # pragma: no cover
                print(f"[sentinel.feedback] Redis unavailable ({exc}); using local disk")
                self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "disk"

    # ---- low-level table load/save (always the source of truth; never
    # assume an in-memory copy from a previous request is still valid) ----
    def _load_table(self) -> dict[str, dict]:
        try:
            if self._redis is not None:
                blob = self._redis.get(_REDIS_KEY)
                data = json.loads(blob) if blob else {}
                return dict(data)
            by_key: dict[str, dict] = {}
            if self.path.exists():
                for line in self.path.read_text().splitlines():
                    line = line.strip()
                    if line:
                        r = json.loads(line)
                        by_key[r["key"]] = r
            return by_key
        except (json.JSONDecodeError, ValueError) as exc:  # pragma: no cover
            print(f"[sentinel.feedback] could not load feedback ({exc}); starting fresh")
            return {}

    def _save_table(self, table: dict[str, dict]) -> None:
        """Redis: overwrite the whole table blob (cheap, small data).
        Disk: rewrite the jsonl file atomically so a fresh instance loading
        via `_load_table` sees exactly this table, one record per line."""
        if self._redis is not None:
            self._redis.set(_REDIS_KEY, json.dumps(table))
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as fh:
            for rec in table.values():
                fh.write(json.dumps(rec) + "\n")
        tmp.replace(self.path)

    def __len__(self):
        with self._lock:
            return len(self._load_table())

    def record(self, *, cust_id: str, ts, amount: float, label: int,
               kind: str = "disposition", note: str = "") -> dict:
        with self._lock:
            table = self._load_table()
            rec = {
                "key": _key(cust_id, ts, amount), "cust_id": cust_id,
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "amount": round(float(amount), 2), "label": int(label),
                "kind": kind, "note": note, "recorded_at": time.time(),
            }
            table[rec["key"]] = rec
            self._save_table(table)
            return rec

    def label_for(self, cust_id: str, ts, amount: float):
        with self._lock:
            table = self._load_table()
        r = table.get(_key(cust_id, ts, amount))
        return None if r is None else r["label"]

    # ---- training-time helpers --------------------------------------
    def merge_labels(self, events: list[dict]) -> int:
        """Override event labels in place from recorded feedback. Returns #changed."""
        with self._lock:
            table = self._load_table()
        changed = 0
        for e in events:
            if e.get("type") != "txn":
                continue
            r = table.get(_key(e["cust_id"], e["ts"], e["amount"]))
            lbl = None if r is None else r["label"]
            if lbl is not None and lbl != e.get("label", 0):
                e["label"] = lbl
                changed += 1
        return changed

    @staticmethod
    def sample_weights(events_txn: list[dict], now: datetime | None = None,
                       maturity_days: float = 45.0) -> np.ndarray:
        """Confirmed fraud -> x3. Transactions younger than `maturity_days`
        (label may still change) -> linearly down to x0.3 at age 0."""
        now = now or max((e["ts"] for e in events_txn), default=datetime.utcnow())
        w = np.ones(len(events_txn))
        for i, e in enumerate(events_txn):
            if e.get("label", 0) == 1:
                w[i] = 3.0
            age = (now - e["ts"]).total_seconds() / 86400.0
            if age < maturity_days:
                w[i] *= 0.3 + 0.7 * max(age, 0.0) / maturity_days
        return w
