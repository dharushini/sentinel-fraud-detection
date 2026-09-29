"""Behavioural feature engineering.

`compute_features(txn, profile, entities, now)` is used for **offline training**
(replaying historical events) and **online scoring** (a live transaction) — the
exact same code path, so there is no train/serve skew.

Two families of signal:
    * behavioural  — relative to *this customer* (amount vs personal norm, tempo,
      geo, device, login history);
    * entity/graph — from :mod:`sentinel.entities` (merchant/BIN/device/payee
      historical fraud rate, ring fan-in/out).
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from typing import Deque, Optional, Tuple

from .config import HIGH_RISK_MCC, IMPOSSIBLE_TRAVEL_KMH, IMPOSSIBLE_TRAVEL_MIN_KM
from .entities import ENTITY_FEATURES, EntityRegistry

EARTH_RADIUS_KM = 6371.0
_MAX_GAP_SECONDS = 30 * 24 * 3600
_IMPOSSIBLE_TRAVEL_MAX_GAP_S = 3 * 3600      # >3 h apart? it's a flight, not a jump

BEHAVIOUR_FEATURES = [
    "amount", "amount_log", "amount_z", "amount_to_max",
    "hour", "is_night", "is_weekend",
    "new_merchant", "new_country", "new_device", "new_beneficiary", "is_foreign",
    "dist_from_last_km", "speed_kmh", "impossible_travel", "secs_since_last",
    "txn_count_5m", "txn_count_1h", "txn_count_24h",
    "amt_sum_1h", "amt_sum_24h",
    "distinct_merchants_24h", "distinct_countries_24h",
    "failed_logins_1h", "logins_1h",
    "account_age_days", "high_risk_mcc",
    "channel_pos", "channel_online", "channel_atm", "channel_transfer",
]
# behavioural-sequence head: how far this transaction departs from the customer's
# established *sequence* of behaviour (regime change, drum-beat repetition, a
# token they've never produced) — see `_sequence_signals`
SEQUENCE_FEATURES = ["seq_surprise", "seq_new_token", "seq_repeat_5", "seq_regime_kl"]

FEATURE_COLUMNS = BEHAVIOUR_FEATURES + SEQUENCE_FEATURES + ENTITY_FEATURES


def _token(txn: dict) -> str:
    """Discrete behaviour token: (category, channel, log-amount bucket).

    Divisor tuned for INR amounts (bucket 6 saturates around ~22,000, a
    genuinely large single transaction) so the bucket still discriminates
    across the realistic spend range instead of most transactions collapsing
    into the top bucket.
    """
    bucket = min(6, int(math.log1p(max(float(txn.get("amount", 0.0)), 0.0)) / 1.43))
    return f"{txn.get('mcc', '?')}|{txn.get('channel', '?')}|{bucket}"


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class ProfileState:
    """Rolling behavioural fingerprint for one customer. Updated only with
    transactions the bank let through — a blocked txn never redefines 'normal'."""

    __slots__ = (
        "cust_id", "home_country", "home_lat", "home_lon", "account_open",
        "n", "mean", "_m2", "max_amount",
        "merchants", "countries", "devices", "beneficiaries",
        "last_ts", "last_lat", "last_lon", "last_country", "recent", "logins",
        "token_counts", "recent_tokens",
        "peak_5m", "peak_1h", "_peak_pending",
    )

    def __init__(self, cust_id, home_country, home_lat, home_lon, account_open):
        self.cust_id = cust_id
        self.home_country = home_country
        self.home_lat = home_lat
        self.home_lon = home_lon
        self.account_open = account_open
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.max_amount = 0.0
        self.merchants: set[str] = set()
        self.countries: set[str] = {home_country}
        self.devices: set[str] = set()
        self.beneficiaries: set[str] = set()
        self.last_ts: Optional[datetime] = None
        self.last_lat: Optional[float] = None
        self.last_lon: Optional[float] = None
        self.last_country: Optional[str] = None
        self.recent: Deque[Tuple[datetime, float, str, str]] = deque()
        self.logins: Deque[Tuple[datetime, int]] = deque()
        self.token_counts: dict[str, int] = {}          # all-time behaviour tokens
        self.recent_tokens: Deque[str] = deque(maxlen=12)
        # personal velocity baseline: the busiest 5-minute / 1-hour spells seen
        # among this customer's ALLOWED transactions (so an attacker's stopped
        # burst never raises it). Velocity rules compare against it.
        self.peak_5m = 0
        self.peak_1h = 0
        self._peak_pending: Deque[Tuple[datetime, int, int]] = deque()

    @property
    def std(self) -> float:
        return math.sqrt(self._m2 / (self.n - 1)) if self.n > 1 else 0.0

    def _win(self, now, seconds):
        return [x for x in self.recent if (now - x[0]).total_seconds() <= seconds]

    def _failed_logins(self, now, seconds):
        return sum(1 for (t, ok) in self.logins if ok == 0 and (now - t).total_seconds() <= seconds)

    def _logins(self, now, seconds):
        return sum(1 for (t, _ok) in self.logins if (now - t).total_seconds() <= seconds)

    def update(self, txn: dict) -> None:
        amt = float(txn["amount"])
        self.n += 1
        d = amt - self.mean
        self.mean += d / self.n
        self._m2 += d * (amt - self.mean)
        self.max_amount = max(self.max_amount, amt)
        self.merchants.add(txn["merchant_id"])
        self.countries.add(txn["country"])
        self.devices.add(txn["device_id"])
        if txn.get("beneficiary"):
            self.beneficiaries.add(txn["beneficiary"])
        ts = txn["ts"]
        self.last_ts, self.last_country = ts, txn["country"]
        self.last_lat, self.last_lon = float(txn["lat"]), float(txn["lon"])
        self.recent.append((ts, amt, txn["merchant_id"], txn["country"]))
        # A busy spell only becomes part of the baseline once it is over an
        # hour old: otherwise an attacker's first (still-allowed) probes would
        # raise the bar that their own burst is then measured against.
        pend = getattr(self, "_peak_pending", None)
        if pend is None:
            pend = self._peak_pending = deque()
        pend.append((ts, len(self._win(ts, 300)), len(self._win(ts, 3600))))
        self._trim(self.recent, ts)
        tok = _token(txn)
        self.token_counts[tok] = self.token_counts.get(tok, 0) + 1
        self.recent_tokens.append(tok)

    def velocity_baseline(self, now: datetime) -> dict:
        """The customer's established busiest 5-minute / 1-hour spells (spells
        at least an hour old) and how many allowed transactions they have."""
        pend = getattr(self, "_peak_pending", None) or deque()
        p5, p1 = getattr(self, "peak_5m", 0), getattr(self, "peak_1h", 0)
        while pend and (now - pend[0][0]).total_seconds() >= 3600:
            _t, c5, c1 = pend.popleft()
            p5, p1 = max(p5, c5), max(p1, c1)
        self.peak_5m, self.peak_1h = p5, p1
        return {"peak_5m": float(p5), "peak_1h": float(p1), "n": float(self.n)}

    def add_login(self, ts, success, device_id="") -> None:
        self.logins.append((ts, int(success)))
        self._trim(self.logins, ts)
        if success and device_id:
            self.devices.add(device_id)

    @staticmethod
    def _trim(dq, now, horizon=24 * 3600):
        while dq and (now - dq[0][0]).total_seconds() > horizon:
            dq.popleft()


def compute_features(txn: dict, ps: ProfileState,
                     entities: EntityRegistry | None, now: datetime) -> dict:
    amt = float(txn["amount"])
    std = ps.std or max(ps.mean * 0.35, 1.0)
    mean = ps.mean or amt

    if ps.last_ts is not None:
        secs = min((now - ps.last_ts).total_seconds(), _MAX_GAP_SECONDS)
        dist = haversine(ps.last_lat, ps.last_lon, float(txn["lat"]), float(txn["lon"]))
        speed = dist / max(secs / 3600.0, 1 / 60.0)
    else:
        secs, dist, speed = float(_MAX_GAP_SECONDS), 0.0, 0.0
    # "impossible" only if the hop is faster than any flight *and* the time gap
    # is too short to have flown it — a 900 km/h average over 9 h is just a
    # long-haul flight, not teleportation.
    impossible = 1.0 if (speed > IMPOSSIBLE_TRAVEL_KMH
                         and dist > IMPOSSIBLE_TRAVEL_MIN_KM
                         and secs < _IMPOSSIBLE_TRAVEL_MAX_GAP_S) else 0.0

    w5m, w1h, w24 = ps._win(now, 300), ps._win(now, 3600), ps._win(now, 86400)
    hour, channel = now.hour, txn["channel"]
    benef = txn.get("beneficiary") or ""

    feat = {
        "amount": amt,
        "amount_log": math.log1p(amt),
        "amount_z": (amt - mean) / std,
        "amount_to_max": amt / (ps.max_amount or amt),
        "hour": float(hour),
        "is_night": 1.0 if (hour < 6 or hour >= 23) else 0.0,
        "is_weekend": 1.0 if now.weekday() >= 5 else 0.0,
        "new_merchant": 0.0 if txn["merchant_id"] in ps.merchants else 1.0,
        "new_country": 0.0 if txn["country"] in ps.countries else 1.0,
        "new_device": 0.0 if txn["device_id"] in ps.devices else 1.0,
        "new_beneficiary": 0.0 if (not benef or benef in ps.beneficiaries) else 1.0,
        "is_foreign": 0.0 if txn["country"] == ps.home_country else 1.0,
        "dist_from_last_km": dist,
        "speed_kmh": speed,
        "impossible_travel": impossible,
        "secs_since_last": secs,
        "txn_count_5m": float(len(w5m)),
        "txn_count_1h": float(len(w1h)),
        "txn_count_24h": float(len(w24)),
        "amt_sum_1h": float(sum(x[1] for x in w1h)),
        "amt_sum_24h": float(sum(x[1] for x in w24)),
        "distinct_merchants_24h": float(len({x[2] for x in w24})),
        "distinct_countries_24h": float(len({x[3] for x in w24})),
        "failed_logins_1h": float(ps._failed_logins(now, 3600)),
        "logins_1h": float(ps._logins(now, 3600)),
        "account_age_days": max((now - ps.account_open).days, 0) * 1.0,
        "high_risk_mcc": 1.0 if txn["mcc"] in HIGH_RISK_MCC else 0.0,
        "channel_pos": 1.0 if channel == "pos" else 0.0,
        "channel_online": 1.0 if channel == "online" else 0.0,
        "channel_atm": 1.0 if channel == "atm" else 0.0,
        "channel_transfer": 1.0 if channel == "transfer" else 0.0,
    }
    feat.update(_sequence_signals(txn, ps))
    if entities is not None:
        feat.update(entities.snapshot_features(txn, now))
    else:
        feat.update({k: 0.0 for k in ENTITY_FEATURES})
    return feat


def _sequence_signals(txn: dict, ps: ProfileState) -> dict:
    """Behavioural-sequence head (no deep learning): a smoothed unigram model of
    the customer's own behaviour tokens.

        seq_surprise   -log P(token | this customer's history), 0..1
        seq_new_token  the customer has never produced this token before
        seq_repeat_5   fraction of the last 5 tokens identical to this one
                       (card-testing / bust-out hammer one token)
        seq_regime_kl  KL(recent-window token mix ‖ all-time mix) — a takeover
                       that switches a grocery/pos customer to wire transfers
    """
    tok = _token(txn)
    counts = ps.token_counts
    total = sum(counts.values())
    if total == 0:
        return {"seq_surprise": 0.0, "seq_new_token": 0.0,
                "seq_repeat_5": 0.0, "seq_regime_kl": 0.0}

    vocab = len(counts) + 1
    p_tok = (counts.get(tok, 0) + 0.5) / (total + 0.5 * vocab)
    surprise = min(-math.log(p_tok) / 8.0, 1.0)

    last5 = list(ps.recent_tokens)[-5:]
    repeat5 = (sum(1 for t in last5 if t == tok) / len(last5)) if last5 else 0.0

    recent = list(ps.recent_tokens)[-10:]
    kl = 0.0
    if len(recent) >= 4:
        rc: dict[str, int] = {}
        for t in recent:
            rc[t] = rc.get(t, 0) + 1
        rn = len(recent)
        for t, c in rc.items():
            q = c / rn
            p = (counts.get(t, 0) + 0.5) / (total + 0.5 * vocab)
            kl += q * math.log(q / p)
        kl = min(kl / 4.0, 1.0)

    return {"seq_surprise": surprise,
            "seq_new_token": 0.0 if tok in counts else 1.0,
            "seq_repeat_5": repeat5,
            "seq_regime_kl": kl}


def row_to_vector(feat: dict) -> list[float]:
    return [float(feat[c]) for c in FEATURE_COLUMNS]
