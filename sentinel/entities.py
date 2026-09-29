"""Entity aggregation + graph features.

Per-customer behaviour (``features.ProfileState``) answers *"is this normal for
this person?"*. This module answers the other half:

    * **Entity risk** — has this *merchant / card BIN / device / beneficiary*
      account been seen mostly on fraud before?  (Bayesian-smoothed, time-decayed.)
    * **Graph** — how many *distinct customers* touch this device or pay this
      beneficiary?  A mule account collecting from 6 people, or one device
      logging into 6 accounts, is a fraud ring even if each single transaction
      looks fine.

All statistics decay with a half-life so a compromised-then-cleaned merchant
recovers, and a newly abused one lights up fast.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict

from .config import ENTITY_HALFLIFE_DAYS, ENTITY_PRIOR_STRENGTH

ENTITY_FEATURES = [
    "merchant_fraud_rate", "merchant_txn_count", "merchant_age_days",
    "bin_fraud_rate",
    "device_fraud_rate", "device_customer_fanout", "device_is_global_new",
    "beneficiary_fraud_rate", "beneficiary_customer_fanin",
    "beneficiary_is_global_new", "beneficiary_txn_count",
    "entity_max_fraud_rate", "ring_size",
]


def _decay_factor(since: datetime, now: datetime) -> float:
    dt_days = max((now - since).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (dt_days / ENTITY_HALFLIFE_DAYS)


@dataclass
class _Stat:
    n: float = 0.0            # time-decayed transaction count
    f: float = 0.0            # time-decayed fraud count
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    customers: set = field(default_factory=set)
    # per-customer decayed [n, f, last_ts] — lets a rate be read "excluding
    # this customer's own history" (see rate_excluding)
    by_cust: dict = field(default_factory=dict)

    def _decay(self, now: datetime) -> None:
        if self.last_ts is not None:
            dt_days = max((now - self.last_ts).total_seconds() / 86400.0, 0.0)
            k = 0.5 ** (dt_days / ENTITY_HALFLIFE_DAYS)
            self.n *= k
            self.f *= k
        self.last_ts = now

    def observe(self, now: datetime, label: int, cust_id: str) -> None:
        self._decay(now)
        self.n += 1.0
        self.f += 1.0 if label == 1 else 0.0
        self.customers.add(cust_id)
        if self.first_ts is None:
            self.first_ts = now
        bc = getattr(self, "by_cust", None)
        if bc is None:                      # snapshot from before by_cust existed
            bc = self.by_cust = {}
        n, f, t = bc.get(cust_id, (0.0, 0.0, now))
        k = _decay_factor(t, now)
        bc[cust_id] = (n * k + 1.0, f * k + (1.0 if label == 1 else 0.0), now)

    def rate(self, prior: float, now: datetime) -> float:
        # decayed counts as of `now`, smoothed toward the global prior
        k = 0.5 ** (max((now - self.last_ts).total_seconds() / 86400.0, 0.0)
                    / ENTITY_HALFLIFE_DAYS) if self.last_ts else 1.0
        n, f = self.n * k, self.f * k
        return (f + prior * ENTITY_PRIOR_STRENGTH) / (n + ENTITY_PRIOR_STRENGTH)

    def rate_excluding(self, cust_id: str, prior: float, now: datetime) -> float:
        """Fraud rate of this entity across OTHER customers only.

        For a customer's own device and card this is the signal that matters:
        a device or card other people have had fraud on is a ring / compromise
        indicator. The customer's OWN past fraud on their own device/card is
        not — it means a fraudster once used their phone or card, and after
        that episode (in reality: after the card is reissued and the account
        secured) their normal spending on it is normal again. Counting it made
        every legitimate transaction a past victim made look fraudulent for
        weeks afterwards (held-out evaluation: genuine-customer stops jumped
        from ~0% to 10-19% per day as soon as fraud started being injected).
        """
        k = _decay_factor(self.last_ts, now) if self.last_ts else 1.0
        n, f = self.n * k, self.f * k
        own = (getattr(self, "by_cust", None) or {}).get(cust_id)
        if own:
            ok = _decay_factor(own[2], now)
            n = max(n - own[0] * ok, 0.0)
            f = min(max(f - own[1] * ok, 0.0), n)
        return (f + prior * ENTITY_PRIOR_STRENGTH) / (n + ENTITY_PRIOR_STRENGTH)

    def age_days(self, now: datetime) -> float:
        return (now - self.first_ts).total_seconds() / 86400.0 if self.first_ts else 0.0


class EntityRegistry:
    KINDS = ("merchant", "bin", "device", "beneficiary")

    def __init__(self) -> None:
        self._t: Dict[str, Dict[str, _Stat]] = {k: {} for k in self.KINDS}
        self._prior_n = 1.0
        self._prior_f = 0.003          # start near a plausible base rate

    @property
    def prior(self) -> float:
        return self._prior_f / self._prior_n

    def _get(self, kind: str, key: str) -> _Stat:
        d = self._t[kind]
        s = d.get(key)
        if s is None:
            s = d[key] = _Stat()
        return s

    @staticmethod
    def _keys(txn: dict) -> dict:
        benef = txn.get("beneficiary") or ""
        return {
            "merchant": str(txn.get("merchant_id", "?")),
            "bin": str(txn.get("card_bin", "?")),
            "device": str(txn.get("device_id", "?")),
            "beneficiary": benef,
        }

    # ---- read: features as of `now`, BEFORE observing this txn ------------
    def snapshot_features(self, txn: dict, now: datetime) -> dict:
        keys = self._keys(txn)
        prior = self.prior
        m = self._t["merchant"].get(keys["merchant"])
        b = self._t["bin"].get(keys["bin"])
        d = self._t["device"].get(keys["device"])
        bn = self._t["beneficiary"].get(keys["beneficiary"]) if keys["beneficiary"] else None

        cid = str(txn.get("cust_id", "?"))
        mr = m.rate(prior, now) if m else prior
        # device + card: other customers' fraud only (see _Stat.rate_excluding)
        br = b.rate_excluding(cid, prior, now) if b else prior
        dr = d.rate_excluding(cid, prior, now) if d else prior
        nr = bn.rate(prior, now) if bn else prior

        dev_fanout = len(d.customers) if d else 0
        ben_fanin = len(bn.customers) if bn else 0

        return {
            "merchant_fraud_rate": mr,
            "merchant_txn_count": float(m.n if m else 0.0),
            "merchant_age_days": float(m.age_days(now) if m else 0.0),
            "bin_fraud_rate": br,
            "device_fraud_rate": dr,
            "device_customer_fanout": float(dev_fanout),
            "device_is_global_new": 0.0 if d else 1.0,
            "beneficiary_fraud_rate": nr,
            "beneficiary_customer_fanin": float(ben_fanin),
            "beneficiary_is_global_new": 0.0 if bn else (1.0 if keys["beneficiary"] else 0.0),
            "beneficiary_txn_count": float(bn.n if bn else 0.0),
            "entity_max_fraud_rate": max(mr, br, dr, nr),
            "ring_size": float(max(dev_fanout, ben_fanin)),
        }

    # ---- write ---------------------------------------------------------
    def observe(self, txn: dict, label: int, now: datetime) -> None:
        for kind, key in self._keys(txn).items():
            if kind == "beneficiary" and not key:
                continue
            self._get(kind, key).observe(now, label, str(txn.get("cust_id", "?")))
        self._prior_n += 1.0
        self._prior_f += 1.0 if label == 1 else 0.0

    def prune(self, now: datetime, max_idle_days: float = 90.0,
              keep_fraud: bool = True) -> int:
        """Drop entities that are stale *and* carry little signal, to bound
        memory and snapshot size. Fraudulent entities are always kept."""
        removed = 0
        for kind, d in self._t.items():
            for key in list(d):
                s = d[key]
                idle = (now - s.last_ts).total_seconds() / 86400.0 if s.last_ts else 1e9
                if idle > max_idle_days and s.n < 1.0 and not (keep_fraud and s.f > 0):
                    del d[key]
                    removed += 1
        return removed

    # ---- forensics: real connections for the entity graph -------------
    def connections(self, txn: dict, now: datetime, max_per_kind: int = 8) -> dict:
        """Real shared-entity links for this transaction: which OTHER customers
        have touched the same device, been paid via the same beneficiary
        account, used the same card BIN, or shopped at the same merchant.
        This is exactly the signal behind `device_customer_fanout`,
        `beneficiary_customer_fanin` and `ring_size` — surfaced as edges
        instead of a single number, for the forensics graph. No synthetic or
        external data: every edge here is a transaction Sentinel has actually
        scored."""
        keys = self._keys(txn)
        cust_id = str(txn.get("cust_id", "?"))
        prior = self.prior
        links = []
        for kind in ("device", "beneficiary", "bin", "merchant"):
            key = keys[kind]
            if not key or key == "?":
                continue
            stat = self._t[kind].get(key)
            if not stat:
                continue
            others = sorted(c for c in stat.customers if c != cust_id)[:max_per_kind]
            if not others:
                continue
            links.append({
                "kind": kind,
                "key": key,
                "fraud_rate": round(stat.rate(prior, now), 4),
                "shared_with": others,
                "total_customers": len(stat.customers),
            })
        return {"cust_id": cust_id, "links": links}

    def confirm_fraud(self, txn: dict, now: datetime) -> None:
        """Late signal from a chargeback / analyst — bump fraud counters only."""
        for kind, key in self._keys(txn).items():
            if kind == "beneficiary" and not key:
                continue
            s = self._get(kind, key)
            s._decay(now)
            s.f += 1.0
            # attribute it to the customer too, so rate_excluding() stays consistent
            cid = str(txn.get("cust_id", "?"))
            bc = getattr(s, "by_cust", None)
            if bc is None:
                bc = s.by_cust = {}
            n, f, t = bc.get(cid, (0.0, 0.0, now))
            k = _decay_factor(t, now)
            bc[cid] = (max(n * k, f * k + 1.0), f * k + 1.0, now)
        self._prior_f += 1.0
