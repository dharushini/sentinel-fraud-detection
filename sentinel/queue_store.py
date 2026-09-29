"""Multi-analyst case queue.

Every transaction that lands as REVIEW or CHALLENGE needs a human to look at
it. In a single-analyst demo that's just "click a card in the feed" — but a
real fraud desk has *several* analysts pulling from one shared queue, and the
thing that actually breaks in production is two people working the same case
at once. This store gives the queue real claim/release/resolve semantics so
that's visible and testable, not just implied.

Two backends, same pattern as state.SnapshotStore:
  * local disk (default) — a small JSON file, fine for a single long-lived
    process (e.g. `uvicorn` run locally or on a normal VM/container host).
  * Redis (SENTINEL_REDIS_URL set) — required on a serverless host like
    Vercel, where every request can hit a *different* instance with its own
    empty filesystem and no shared memory. Without Redis there, the queue
    would appear to reset between clicks, which would misrepresent a
    genuinely-working feature as broken.

Because a serverless instance is not guaranteed to survive between requests,
every mutating method here re-reads the full table, applies the change, and
writes it straight back — it does NOT trust an in-memory copy to still be
current the way a long-lived process safely could. That costs a little
latency (one extra read per write) in exchange for correctness under
Vercel's execution model.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from threading import RLock

from .config import QUEUE_PATH, REDIS_URL

OPEN = "open"
CLAIMED = "claimed"
RESOLVED = "resolved"

_REDIS_KEY = "sentinel:queue:v1"


class QueueStore:
    def __init__(self, path: Path = QUEUE_PATH, redis_url: str = REDIS_URL):
        self.path = Path(path)
        self._lock = RLock()
        self._redis = None
        if redis_url:
            try:
                import redis  # type: ignore
                self._redis = redis.Redis.from_url(redis_url)
                self._redis.ping()
            except Exception as exc:  # pragma: no cover
                print(f"[sentinel.queue_store] Redis unavailable ({exc}); using local disk")
                self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "disk"

    # ---- low-level table load/save (always the source of truth; never
    # assume an in-memory copy from a previous request is still valid) ----
    def _load_table(self) -> dict[int, dict]:
        try:
            if self._redis is not None:
                blob = self._redis.get(_REDIS_KEY)
                data = json.loads(blob) if blob else {}
            elif self.path.exists():
                data = json.loads(self.path.read_text() or "{}")
            else:
                data = {}
            return {int(k): v for k, v in data.items()}
        except (json.JSONDecodeError, ValueError) as exc:  # pragma: no cover
            print(f"[sentinel.queue_store] could not load queue ({exc}); starting fresh")
            return {}

    def _save_table(self, table: dict[int, dict]) -> None:
        blob = json.dumps(table)
        if self._redis is not None:
            self._redis.set(_REDIS_KEY, blob)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(blob)
        tmp.replace(self.path)

    def enqueue(self, case_id: int, *, action: str, risk: float, cust_id: str, amount: float):
        """Add a case to the queue if it isn't already tracked."""
        with self._lock:
            table = self._load_table()
            if case_id in table:
                return table[case_id]
            entry = {
                "case_id": case_id, "action": action, "risk": risk,
                "cust_id": cust_id, "amount": amount,
                "status": OPEN, "claimed_by": None, "claimed_at": None,
                "resolved_by": None, "resolved_at": None, "resolution": None,
                "queued_at": time.time(),
            }
            table[case_id] = entry
            self._save_table(table)
            return entry

    def claim(self, case_id: int, analyst: str) -> dict:
        with self._lock:
            table = self._load_table()
            entry = table.get(case_id)
            if entry is None:
                raise KeyError(f"case {case_id} is not in the queue")
            if entry["status"] == CLAIMED and entry["claimed_by"] != analyst:
                raise PermissionError(
                    f"already claimed by {entry['claimed_by']}")
            if entry["status"] == RESOLVED:
                raise PermissionError("already resolved")
            entry["status"] = CLAIMED
            entry["claimed_by"] = analyst
            entry["claimed_at"] = time.time()
            self._save_table(table)
            return entry

    def release(self, case_id: int, analyst: str) -> dict:
        """Give a case back to the pool without resolving it (e.g. an
        analyst realizes they need a second opinion)."""
        with self._lock:
            table = self._load_table()
            entry = table.get(case_id)
            if entry is None:
                raise KeyError(f"case {case_id} is not in the queue")
            if entry["status"] == CLAIMED and entry["claimed_by"] != analyst:
                raise PermissionError(
                    f"claimed by {entry['claimed_by']}, not {analyst}")
            entry["status"] = OPEN
            entry["claimed_by"] = None
            entry["claimed_at"] = None
            self._save_table(table)
            return entry

    def resolve(self, case_id: int, analyst: str, resolution: str) -> dict | None:
        """Mark a case resolved. Called alongside /feedback so the queue
        reflects the same disposition an analyst already recorded."""
        with self._lock:
            table = self._load_table()
            entry = table.get(case_id)
            if entry is None:
                return None
            entry["status"] = RESOLVED
            entry["resolved_by"] = analyst
            entry["resolved_at"] = time.time()
            entry["resolution"] = resolution
            self._save_table(table)
            return entry

    def snapshot(self, status: str | None = None) -> list[dict]:
        with self._lock:
            items = list(self._load_table().values())
        if status:
            items = [i for i in items if i["status"] == status]
        items.sort(key=lambda e: e["queued_at"], reverse=True)
        return items

    def counts(self) -> dict:
        with self._lock:
            items = list(self._load_table().values())
        return {
            "open": sum(1 for i in items if i["status"] == OPEN),
            "claimed": sum(1 for i in items if i["status"] == CLAIMED),
            "resolved": sum(1 for i in items if i["status"] == RESOLVED),
        }
