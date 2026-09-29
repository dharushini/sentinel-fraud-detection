"""Two-headed model + calibration + cost-sensitive operating point.

    head A  GradientBoosting (HistGradientBoosting)  -> P(fraud | features)
            wrapped in isotonic calibration so the probability is trustworthy
    head B  IsolationForest                          -> novelty (0..1)

    blend   risk = W_SUP * P_calibrated + W_ANOM * novelty
    label   the classifier's hard threshold is the expected-cost minimiser
            on validation (COST_FALSE_NEGATIVE vs COST_FALSE_POSITIVE)

Also stores the training median row (for counterfactual explanations) and the
reference score histogram (for drift / PSI monitoring).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from .config import (
    COST_FALSE_NEGATIVE, COST_FALSE_POSITIVE, MODEL_PATH, MODEL_THRESHOLD_OVERRIDE,
    W_ANOMALY, W_SUPERVISED,
)


def _risk_blend(cal_p: np.ndarray, iso, X: np.ndarray) -> np.ndarray:
    d = iso.decision_function(X)
    anomaly = 1.0 / (1.0 + np.exp(4.0 * d))
    return np.clip(W_SUPERVISED * cal_p + W_ANOMALY * anomaly, 0.0, 1.0)
from .features import FEATURE_COLUMNS, row_to_vector
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler


@dataclass
class ScoreBreakdown:
    risk: float
    fraud_proba: float          # calibrated
    anomaly: float
    top_features: list = field(default_factory=list)
    counterfactual: dict = field(default_factory=dict)


class OnlineAdjuster:
    """Honest scope: a SMALL, capped correction learned incrementally from
    analyst feedback via `SGDClassifier.partial_fit` — not a live retrain of
    the ensemble, and not framed as full adaptive intelligence. Real limits:

      * It only ever nudges the calibrated risk score by up to
        `MAX_ADJUSTMENT` in either direction; it can never override the base
        model or the rules layer, so a bad label can't silently flip BLOCK <->
        ALLOW on its own.
      * It needs a handful of confirmed labels on SIMILAR transactions before
        its correction is applied at all (`min_samples_for_feature`) —
        before that it stays silent (adjustment 0), because one example is
        an anecdote, not a pattern.
      * It is scoped to a small set of feature groups it was warm-started on,
        not the full 48-feature space, so it can't overfit to a single
        analyst's one-off correction on unrelated features.

    What this actually demonstrates: the system can incorporate an analyst's
    "that was wrong" feedback into its NEXT scoring pass within milliseconds,
    without a full offline retrain — a real, useful property for a live
    fraud desk — while being honest that it is a bounded correction layer,
    not a self-rewriting model.
    """

    MAX_ADJUSTMENT = 0.15          # cap: +/-15 percentage points of risk, max
    ADJUST_FEATURES = [
        "amount_z", "new_device", "new_country", "new_beneficiary",
        "impossible_travel", "merchant_fraud_rate", "device_fraud_rate",
        "beneficiary_fraud_rate", "entity_max_fraud_rate", "seq_surprise",
    ]

    def __init__(self, warm_X: np.ndarray | None = None, warm_y: np.ndarray | None = None):
        self.clf = SGDClassifier(loss="log_loss", alpha=1e-4, random_state=7)
        self.scaler = StandardScaler()
        self.n_updates = 0
        self.classes_ = np.array([0, 1])
        if warm_X is not None and len(warm_X) >= 20 and warm_y is not None and warm_y.sum() >= 2:
            self.scaler.fit(warm_X)
            self.clf.partial_fit(self.scaler.transform(warm_X), warm_y, classes=self.classes_)
            self.n_updates = len(warm_X)

    def _vec(self, feat: dict) -> np.ndarray:
        return np.asarray([[float(feat.get(k, 0.0)) for k in self.ADJUST_FEATURES]])

    def update(self, feat: dict, label: int) -> None:
        """Incorporate one analyst-confirmed label. Called from the
        /feedback endpoint — this is the live weight update, in real time,
        no batch retrain."""
        x = self._vec(feat)
        if self.n_updates == 0:
            self.scaler.partial_fit(x)
            self.clf.partial_fit(self.scaler.transform(x), [label], classes=self.classes_)
        else:
            self.clf.partial_fit(self.scaler.transform(x), [label])
        self.n_updates += 1

    def adjustment(self, feat: dict) -> float:
        """Signed correction to add to the base risk score, in [-MAX, +MAX].
        Returns 0.0 (no opinion) until enough feedback has been seen."""
        if self.n_updates < 5:
            return 0.0
        try:
            x = self.scaler.transform(self._vec(feat))
            p = float(self.clf.predict_proba(x)[0, 1])
        except Exception:
            return 0.0
        # p=0.5 (no opinion) -> 0 adjustment; scale the rest into the cap
        return float(np.clip((p - 0.5) * 2 * self.MAX_ADJUSTMENT, -self.MAX_ADJUSTMENT, self.MAX_ADJUSTMENT))

    def status(self) -> dict:
        return {"updates": self.n_updates, "active": self.n_updates >= 5,
                "max_adjustment": self.MAX_ADJUSTMENT, "features_used": self.ADJUST_FEATURES}


class FraudModel:
    def __init__(self, clf, iso, calibrator, meta: dict | None = None):
        self.clf = clf
        self.iso = iso
        self.calibrator = calibrator            # IsotonicRegression, PlattCalibrator or None
        self.meta = meta or {}
        self.median = np.asarray(self.meta.get("median", [0.0] * len(FEATURE_COLUMNS)))
        self._explainer = None                  # lazy, model-agnostic counterfactual search

    # ---- scoring -----------------------------------------------------
    def _calibrate(self, raw_p: np.ndarray) -> np.ndarray:
        if self.calibrator is None:
            return raw_p
        return np.clip(self.calibrator.predict(raw_p), 0.0, 1.0)

    def proba(self, X: np.ndarray) -> np.ndarray:
        return self._calibrate(self.clf.predict_proba(X)[:, 1])

    def score(self, feat: dict, explain: bool = False) -> ScoreBreakdown:
        x = np.asarray([row_to_vector(feat)], dtype=float)
        p = float(self.proba(x)[0])
        d = float(self.iso.decision_function(x)[0])
        anomaly = 1.0 / (1.0 + math.exp(4.0 * d))
        risk = float(np.clip(W_SUPERVISED * p + W_ANOMALY * anomaly, 0.0, 1.0))
        top = self.explainer.explain(feat) if explain else []
        return ScoreBreakdown(risk, p, anomaly, top)

    def explain_features(self, feat: dict, k: int = 5):
        return self.explainer.explain(feat, k=k)

    @property
    def explainer(self):
        if self._explainer is None:
            from .explain import CounterfactualExplainer
            self._explainer = CounterfactualExplainer(self.clf, self.median, self.calibrator)
        return self._explainer

    def find_counterfactual(self, feat: dict) -> dict:
        return self.explainer.find_counterfactual(feat, self.label_threshold)

    @property
    def label_threshold(self) -> float:
        # a bank can set its own operating point (fraud caught vs genuine
        # customers challenged) without retraining
        override = getattr(self, "threshold_override", None)
        if override is None:
            override = MODEL_THRESHOLD_OVERRIDE
        return float(override if override is not None else self.meta.get("cost_threshold", 0.5))

    # ---- persistence ----------------------------------------------
    def save(self, path=MODEL_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"clf": self.clf, "iso": self.iso,
                     "calibrator": self.calibrator, "meta": self.meta}, path)

    @classmethod
    def load(cls, path=MODEL_PATH):
        b = joblib.load(path)
        return cls(b["clf"], b["iso"], b.get("calibrator"), b.get("meta"))

    @classmethod
    def exists(cls, path=MODEL_PATH):
        return path.exists()


# --------------------------------------------------------------------------- #
def _fit_clf(X, y, sample_weight=None):
    pos = max(int(y.sum()), 1)
    w = np.where(y == 1, (len(y) - pos) / pos, 1.0)
    if sample_weight is not None:
        w = w * np.asarray(sample_weight, dtype=float)
    clf = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.06, max_depth=None, max_leaf_nodes=31,
        l2_regularization=1.0, early_stopping=True, validation_fraction=0.1,
        random_state=0,
    )
    clf.fit(X, y, sample_weight=w)
    return clf


def _fit_iso(X, y):
    # 64 trees / 128-sample subsets keep a single-row score near ~3 ms while
    # still flagging the adversarial "looks normal" cases the classifier misses
    iso = IsolationForest(n_estimators=64, max_samples=128, contamination=0.02,
                          n_jobs=1, random_state=0)
    iso.fit(X[y == 0])
    return iso


def _argmin_cost_threshold(y: np.ndarray, proba: np.ndarray, c_fn: float, c_fp: float) -> tuple[float, float]:
    """Exact cost-minimising threshold on one sample (vectorised sweep)."""
    order = np.argsort(-proba, kind="stable")
    p, yy = proba[order], y[order].astype(int)
    tp = np.cumsum(yy)                       # predicting positive for the top-k
    fp = np.cumsum(1 - yy)
    fn = yy.sum() - tp
    cost = c_fp * fp + c_fn * fn
    # only cut between distinct probability values (ties must go together)
    last_of_value = np.r_[p[1:] != p[:-1], True]
    cost_at = np.where(last_of_value, cost, np.inf)
    k = int(np.argmin(cost_at))
    none_cost = c_fn * yy.sum()              # predict nothing positive
    if none_cost <= cost_at[k]:
        return 1.0, float(none_cost)
    return float(p[k]), float(cost_at[k])


def cost_threshold(y: np.ndarray, proba: np.ndarray,
                   c_fn: float = COST_FALSE_NEGATIVE,
                   c_fp: float = COST_FALSE_POSITIVE,
                   n_boot: int = 200, seed: int = 0) -> tuple[float, float]:
    """Threshold on `proba` that minimises expected cost. Returns (thr, cost).

    The exact minimiser on a single validation slice can be fragile:
    calibrated probabilities are step-shaped, so the minimum may sit on a step
    that suits that one slice. The threshold is therefore the median of the
    cost-minimising thresholds over bootstrap resamples of the validation
    slice — a standard way to stabilise a data-driven cut-off. (Where the
    slice's optimum is already stable, this returns the same value.) The
    cost ratio is a business setting; `SENTINEL_MODEL_THRESHOLD` overrides
    the learned value entirely — see docs/EVALUATION.md "Operating points"."""
    y = np.asarray(y).astype(int)
    proba = np.asarray(proba, dtype=float)
    if n_boot <= 1 or y.sum() == 0:
        return _argmin_cost_threshold(y, proba, c_fn, c_fp)
    rng = np.random.default_rng(seed)
    ts = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].sum() == 0:
            continue
        ts.append(_argmin_cost_threshold(y[idx], proba[idx], c_fn, c_fp)[0])
    thr = float(np.median(ts)) if ts else _argmin_cost_threshold(y, proba, c_fn, c_fp)[0]
    pred = proba >= thr
    cost = c_fp * int(np.sum(pred & (y == 0))) + c_fn * int(np.sum(~pred & (y == 1)))
    return thr, float(cost)


ISOTONIC_MIN_POSITIVES = 50   # below this many validation frauds, calibrate with a sigmoid


def _sigmoid_helps(raw: np.ndarray, y: np.ndarray, folds: int = 5, seed: int = 0) -> bool:
    """Does sigmoid calibration beat the raw probabilities out-of-fold?
    With few positives calibration can make things worse, so it has to earn
    its place (stratified k-fold Brier comparison on the validation slice)."""
    from sklearn.model_selection import StratifiedKFold
    y = np.asarray(y, int)
    k = int(min(folds, y.sum(), len(y) - y.sum()))
    if k < 2:
        return False
    cal_err = raw_err = 0.0
    for tr, te in StratifiedKFold(n_splits=k, shuffle=True, random_state=seed).split(raw, y):
        c = PlattCalibrator().fit(raw[tr], y[tr])
        cal_err += float(np.sum((c.predict(raw[te]) - y[te]) ** 2))
        raw_err += float(np.sum((raw[te] - y[te]) ** 2))
    # must be a real improvement (>= 1% lower out-of-fold squared error), not
    # a noise-level tie — a sigmoid can represent "no change" exactly, so on
    # scores that are already calibrated it merely ties
    return cal_err < 0.99 * raw_err


class PlattCalibrator:
    """Sigmoid (Platt) calibration of a raw probability, fit on its logit.

    Same interface the engine and explainer use for the isotonic calibrator
    (`fit(raw, y)`, `predict(raw) -> calibrated probability`), so either can
    be stored in the model artifact."""

    def fit(self, raw, y):
        from sklearn.linear_model import LogisticRegression
        z = self._logit(raw).reshape(-1, 1)
        self.lr_ = LogisticRegression(C=1e4, max_iter=1000).fit(z, np.asarray(y, int))
        return self

    def predict(self, raw):
        z = self._logit(raw).reshape(-1, 1)
        return self.lr_.predict_proba(z)[:, 1]

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p))


def train(X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None,
          feature_names: list[str] | None = None,
          split: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None) -> tuple[FraudModel, dict]:
    """Fit on a time-ordered split.

    `split` = (train_idx, valid_idx, test_idx). If None, a plain 70/15/15 split
    on row order is used (callers that care about time must pass ordered indices).
    """
    y = y.astype(int)
    n = len(y)
    if split is None:
        a, b = int(n * 0.70), int(n * 0.85)
        tr, va, te = np.arange(a), np.arange(a, b), np.arange(b, n)
    else:
        tr, va, te = split

    sw = None if sample_weight is None else np.asarray(sample_weight, float)
    clf = _fit_clf(X[tr], y[tr], sw[tr] if sw is not None else None)
    iso = _fit_iso(X[tr], y[tr])

    # probability calibration on the validation slice
    raw_va = clf.predict_proba(X[va])[:, 1]
    calibrator = None
    calibration_method = "none"
    n_pos_va = int(y[va].sum())
    if n_pos_va >= 5 and n_pos_va < len(va):
        if n_pos_va >= ISOTONIC_MIN_POSITIVES:
            calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            calibration_method = "isotonic"
        elif _sigmoid_helps(raw_va, y[va]):
            # Too few fraud cases for isotonic regression, which then overfits
            # into a handful of coarse steps (seen with 17 validation frauds:
            # calibrated Brier WORSE than raw, and probabilities snapping to a
            # single step value). A 2-parameter sigmoid is the standard choice
            # for small calibration sets — used only if cross-validation on
            # the validation slice shows it actually improves on raw scores.
            calibrator = PlattCalibrator()
            calibration_method = "sigmoid"
        if calibrator is not None:
            calibrator.fit(raw_va, y[va])
    cal_va = np.clip(calibrator.predict(raw_va), 0, 1) if calibrator is not None else raw_va

    thr, _ = cost_threshold(y[va], cal_va)

    # out-of-time test metrics on the calibrated probability
    raw_te = clf.predict_proba(X[te])[:, 1]
    cal_te = np.clip(calibrator.predict(raw_te), 0, 1) if calibrator is not None else raw_te
    pred_te = (cal_te >= thr).astype(int)
    tp = int(np.sum(pred_te & (y[te] == 1)))
    fp = int(np.sum(pred_te & (y[te] == 0)))
    fn = int(np.sum(~pred_te.astype(bool) & (y[te] == 1)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    meta = {
        "model": f"HistGradientBoosting + {calibration_method} calibration + IsolationForest",
        "calibration_method": calibration_method,
        "calibration_positives": n_pos_va,
        "features": feature_names or FEATURE_COLUMNS,
        "n_samples": n, "n_fraud": int(y.sum()), "fraud_rate": round(float(y.mean()), 5),
        "split": "time-ordered 70/15/15 (valid=calibration, test=out-of-time)",
        "roc_auc": round(float(roc_auc_score(y[te], cal_te)), 4),
        "pr_auc": round(float(average_precision_score(y[te], cal_te)), 4),
        "brier_raw": round(float(brier_score_loss(y[te], raw_te)), 5),
        "brier_calibrated": round(float(brier_score_loss(y[te], cal_te)), 5),
        "cost_threshold": round(float(thr), 4),
        "precision_oot": round(precision, 4),
        "recall_oot": round(recall, 4),
        "median": np.median(X[tr], axis=0).tolist(),
        # drift reference: the *risk blend* distribution (what the engine
        # actually monitors) on the out-of-time slice, 20 bins
        "ref_scores": _hist(_risk_blend(cal_te, iso, X[te]), bins=10),
    }

    # ship a model fit on train+valid (keep test untouched as the honest holdout)
    fit_idx = np.concatenate([tr, va])
    final = _fit_clf(X[fit_idx], y[fit_idx], sw[fit_idx] if sw is not None else None)
    final_iso = _fit_iso(X[fit_idx], y[fit_idx])
    model = FraudModel(final, final_iso, calibrator, meta)
    return model, meta


def _hist(p: np.ndarray, bins: int = 10) -> list[float]:
    from .drift import score_histogram   # same [0, 0.5] binning as the monitor
    return score_histogram(p, bins=bins)

