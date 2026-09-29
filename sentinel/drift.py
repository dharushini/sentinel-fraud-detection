"""Population Stability Index drift monitoring.

Compares the distribution of live risk scores against the reference distribution
captured on the held-out test set at training time.

Two design choices keep this from crying wolf:

  * only **allowed** traffic is measured — a burst of injected fraud is a
    *different* alarm, not model drift. Drift asks "does my normal population
    still look normal?".
  * "alert" requires the shift to be **sustained** (an EWMA of PSI), so a
    transient blip during an attack wave doesn't flip the badge.

    smoothed PSI < 0.15   stable
    0.15 - 0.35           watch  — investigate
    > 0.35               alert  — population has shifted, consider retraining
"""
from __future__ import annotations

from collections import deque

import numpy as np

from .config import DRIFT_BASELINE_AFTER, PSI_ALERT, PSI_WATCH, PSI_WINDOW

_FLOOR = 0.005      # standard PSI practice: floor each bin at 0.5% so the log
                    # ratio stays finite when a reference bin is (near) empty
PSI_RANGE_HI = 0.5  # allowed traffic is all below this; binning over [0, 0.5]
                    # instead of [0, 1] gives real resolution and avoids the
                    # empty-bin blow-up on a low-entropy score distribution


def score_histogram(scores, bins: int = 10) -> list[float]:
    h, _ = np.histogram(np.clip(scores, 0, PSI_RANGE_HI), bins=bins, range=(0, PSI_RANGE_HI))
    h = h.astype(float) + 1e-6
    return (h / h.sum()).tolist()


def psi(reference: list[float], observed: list[float], bins: int | None = None) -> float:
    ref = np.asarray(reference, dtype=float)
    if len(observed) == 0:
        return 0.0
    bins = bins or len(ref)
    obs_hist, _ = np.histogram(np.clip(observed, 0, PSI_RANGE_HI), bins=bins, range=(0, PSI_RANGE_HI))
    obs = np.maximum(obs_hist.astype(float) / max(obs_hist.sum(), 1), _FLOOR)
    ref = np.maximum(ref / max(ref.sum(), 1e-9), _FLOOR)
    obs /= obs.sum()
    ref /= ref.sum()
    return float(np.sum((obs - ref) * np.log(obs / ref)))


class DriftMonitor:
    MIN_SAMPLES = 400        # PSI is a large-sample statistic

    def __init__(self, reference_scores: list[float]):
        self.reference = list(reference_scores) or [0.1] * 10
        self.window: deque[float] = deque(maxlen=PSI_WINDOW)
        self.count = 0
        self._ewma: float | None = None       # smoothed PSI
        self._baselined = False

    def observe(self, risk: float, allowed: bool = True) -> None:
        self.count += 1
        if not allowed:                       # attack traffic ≠ drift
            return
        self.window.append(float(risk))
        # once enough *real* traffic has flowed, re-baseline the reference on it
        if (not self._baselined and self.count >= DRIFT_BASELINE_AFTER
                and len(self.window) >= min(PSI_WINDOW, 800)):
            self.reference = score_histogram(list(self.window))
            self._ewma = None
            self._baselined = True
        if len(self.window) >= self.MIN_SAMPLES:
            raw = psi(self.reference, list(self.window))
            self._ewma = raw if self._ewma is None else 0.9 * self._ewma + 0.1 * raw

    def status(self) -> dict:
        n = len(self.window)
        if n < self.MIN_SAMPLES or self._ewma is None:
            return {"psi": 0.0, "psi_raw": 0.0, "state": "warming", "window": n,
                    "window_capacity": PSI_WINDOW,
                    "reference_bins": [round(x, 4) for x in self.reference]}
        raw = psi(self.reference, list(self.window))
        v = self._ewma
        state = "alert" if v > PSI_ALERT else "watch" if v > PSI_WATCH else "stable"
        return {"psi": round(v, 4), "psi_raw": round(raw, 4), "state": state,
                "window": n, "window_capacity": PSI_WINDOW,
                "reference_bins": [round(x, 4) for x in self.reference]}
