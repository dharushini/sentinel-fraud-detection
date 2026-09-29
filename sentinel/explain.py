"""Counterfactual evidence and reference-state search for one scored transaction.

The feature probes are one-at-a-time contrasts against the training reference
row. The path search changes coherent feature groups together, greedily looking
for a small hypothetical state that moves the calibrated classifier below its
learned threshold, then prunes unnecessary groups. These are model contrasts,
not causal claims or customer action recommendations.
"""
from __future__ import annotations

import numpy as np

from .features import FEATURE_COLUMNS

# Derived feature families move together so the search does not propose an
# inconsistent amount change while leaving its log and personal z-score untouched.
COUNTERFACTUAL_GROUPS = (
    ("transaction amount", ("amount", "amount_log", "amount_z", "amount_to_max")),
    ("spending velocity", ("txn_count_5m", "txn_count_1h", "txn_count_24h",
                            "amt_sum_1h", "amt_sum_24h")),
    ("customer and channel context", ("new_merchant", "new_device", "new_beneficiary",
                                      "is_foreign", "high_risk_mcc", "channel_pos",
                                      "channel_online", "channel_atm", "channel_transfer")),
    ("location and travel", ("new_country", "dist_from_last_km", "speed_kmh",
                              "impossible_travel", "distinct_countries_24h")),
    ("authentication", ("failed_logins_1h", "logins_1h")),
    ("behaviour sequence", ("seq_surprise", "seq_new_token", "seq_repeat_5", "seq_regime_kl")),
    ("merchant and network history", ("merchant_fraud_rate", "merchant_txn_count",
                                       "merchant_age_days", "bin_fraud_rate",
                                       "device_fraud_rate", "device_customer_fanout",
                                       "device_is_global_new", "beneficiary_fraud_rate",
                                       "beneficiary_customer_fanin", "beneficiary_is_global_new",
                                       "beneficiary_txn_count", "entity_max_fraud_rate",
                                       "ring_size")),
    ("timing and customer history", ("hour", "is_night", "is_weekend", "secs_since_last",
                                     "distinct_merchants_24h", "account_age_days")),
)


class CounterfactualExplainer:
    """Model-agnostic reference contrasts and a compact group counterfactual."""

    mode = "counterfactual"

    def __init__(self, clf, median: np.ndarray, calibrator=None):
        self.clf = clf
        self.median = np.asarray(median, dtype=float)
        self.calibrator = calibrator

    def _probability(self, rows: np.ndarray) -> np.ndarray:
        raw = np.asarray(self.clf.predict_proba(rows)[:, 1], dtype=float)
        if self.calibrator is not None:
            raw = np.asarray(self.calibrator.predict(raw), dtype=float)
        return np.clip(raw, 0.0, 1.0)

    def _row(self, feat: dict) -> np.ndarray:
        return np.asarray([[float(feat[c]) for c in FEATURE_COLUMNS]], dtype=float)

    def explain(self, feat: dict, k: int = 5) -> list[tuple[str, float, float]]:
        """Rank single-feature reference probes; deltas are not additive."""
        x = self._row(feat)
        base = float(self._probability(x)[0])
        grid = np.repeat(x, len(FEATURE_COLUMNS), axis=0)
        for i in range(len(FEATURE_COLUMNS)):
            grid[i, i] = self.median[i]
        probs = self._probability(grid)
        ranked = [
            (name, float(feat[name]), float(base - probs[i]))
            for i, name in enumerate(FEATURE_COLUMNS)
        ]
        ranked.sort(key=lambda item: abs(item[2]), reverse=True)
        return ranked[:k]

    def find_counterfactual(self, feat: dict, threshold: float) -> dict:
        """Find and prune a group-wise reference path below calibrated threshold."""
        original = self._row(feat)[0]
        base_probability = float(self._probability(original.reshape(1, -1))[0])
        result = {
            "method": "grouped_reference_search",
            "found": False,
            "base_probability": round(base_probability, 4),
            "target_probability": round(float(threshold), 4),
            "groups": [],
            "counterfactual_probability": None,
            "note": "Hypothetical model comparison against training-reference values; not causal advice.",
        }
        if base_probability < threshold:
            result["message"] = "The calibrated fraud probability is already below the model threshold."
            return result

        index = {name: i for i, name in enumerate(FEATURE_COLUMNS)}
        candidates = []
        for label, names in COUNTERFACTUAL_GROUPS:
            valid = [index[name] for name in names if name in index]
            if valid:
                candidates.append((label, valid))

        working = original.copy()
        remaining = list(candidates)
        chosen = []
        current_probability = base_probability
        while remaining and current_probability >= threshold:
            trials = []
            for label, cols in remaining:
                trial = working.copy()
                trial[cols] = self.median[cols]
                trials.append(trial)
            probabilities = self._probability(np.asarray(trials))
            best_index = int(np.argmin(probabilities))
            best_probability = float(probabilities[best_index])
            if best_probability >= current_probability:
                break
            label, cols = remaining[best_index]
            current_probability, working = best_probability, trials[best_index]
            chosen.append((label, cols))
            remaining = [(name, group) for name, group in remaining if name != label]

        if current_probability >= threshold:
            result["message"] = "No below-threshold path was found using the configured reference groups."
            result["counterfactual_probability"] = round(current_probability, 4)
            return result

        # Remove any group that is not needed after the greedy path is found.
        for label, cols in list(reversed(chosen)):
            trial = working.copy()
            trial[cols] = original[cols]
            probability = float(self._probability(trial.reshape(1, -1))[0])
            if probability < threshold:
                working = trial
                current_probability = probability
                chosen.remove((label, cols))

        groups = []
        for label, cols in chosen:
            changes = []
            for i in cols:
                before, after = float(original[i]), float(working[i])
                if not np.isclose(before, after, rtol=1e-6, atol=1e-8):
                    changes.append({"feature": FEATURE_COLUMNS[i],
                                    "from": round(before, 4), "to": round(after, 4)})
            if changes:
                groups.append({"label": label, "changes": changes})

        result.update({
            "found": True,
            "groups": groups,
            "counterfactual_probability": round(current_probability, 4),
            "message": "A below-threshold reference path was found.",
        })
        return result
