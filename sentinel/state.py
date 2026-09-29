"""Pluggable state persistence so the engine is restart-safe and can be shared
across instances.

Default: atomic pickle snapshot to ``artifacts/state.snapshot`` on local disk.
If ``SENTINEL_REDIS_URL`` is set and the ``redis`` package is installed, the
snapshot blob is stored in Redis instead, so several API workers share one
warm behavioural state.

The snapshot holds the whole engine state (per-customer profiles, the entity
registry, metrics, sequence counter). It is written every
``config.SNAPSHOT_EVERY`` transactions and on shutdown.
"""
from __future__ import annotations

import os
import pickle
import tempfile
from pathlib import Path

from .config import REDIS_URL, SNAPSHOT_PATH

_REDIS_KEY = "sentinel:state:snapshot"


class SnapshotStore:
    def __init__(self, path: Path = SNAPSHOT_PATH, redis_url: str = REDIS_URL):
        self.path = Path(path)
        self._redis = None
        if redis_url:
            try:
                import redis  # type: ignore
                self._redis = redis.Redis.from_url(redis_url)
                self._redis.ping()
            except Exception as exc:               # pragma: no cover
                print(f"[sentinel.state] Redis unavailable ({exc}); using local disk")
                self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "disk"

    def save(self, obj) -> None:
        blob = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        if self._redis is not None:
            self._redis.set(_REDIS_KEY, blob)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            os.replace(tmp, self.path)             # atomic
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def load(self):
        try:
            if self._redis is not None:
                blob = self._redis.get(_REDIS_KEY)
                return pickle.loads(blob) if blob else None
            if self.path.exists():
                return pickle.loads(self.path.read_bytes())
        except Exception as exc:                   # pragma: no cover
            print(f"[sentinel.state] could not load snapshot ({exc}); starting fresh")
        return None

    def clear(self) -> None:
        if self._redis is not None:
            self._redis.delete(_REDIS_KEY)
        elif self.path.exists():
            self.path.unlink()
