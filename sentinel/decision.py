"""(risk score + calibrated proba + rule hits) -> action + explanation + alert.

This is the layer a fraud-ops team tunes. Thresholds live in config; the
classifier's own hard call uses the cost-minimising threshold learned at
training time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import RISK_BLOCK, RISK_CHALLENGE, RISK_REVIEW
from .model import ScoreBreakdown
from .rules import RuleHit, worst_severity

ACTIONS = ("ALLOW", "REVIEW", "CHALLENGE", "BLOCK")

_PRETTY = {
    "amount_z": "amount vs. personal norm",
    "amount_to_max": "amount vs. personal record",
    "speed_kmh": "travel speed since last txn",
    "impossible_travel": "impossible-travel flag",
    "new_country": "first time in this country",
    "new_device": "unrecognised device",
    "new_merchant": "never-used merchant",
    "new_beneficiary": "never-used payee",
    "failed_logins_1h": "recent failed logins",
    "txn_count_1h": "transactions in last hour",
    "txn_count_5m": "transactions in last 5 min",
    "amt_sum_1h": "amount spent in last hour",
    "distinct_merchants_24h": "distinct merchants in 24 h",
    "high_risk_mcc": "high-risk merchant category",
    "is_night": "overnight timing",
    "secs_since_last": "time since last txn",
    "seq_surprise": "behaviour-sequence surprise",
    "seq_new_token": "never-seen behaviour pattern",
    "seq_repeat_5": "same pattern repeated rapidly",
    "seq_regime_kl": "sudden shift in behaviour mix",
    "merchant_fraud_rate": "merchant historical fraud rate",
    "device_fraud_rate": "device historical fraud rate",
    "bin_fraud_rate": "card-BIN historical fraud rate",
    "beneficiary_fraud_rate": "payee historical fraud rate",
    "entity_max_fraud_rate": "worst entity fraud rate",
    "device_customer_fanout": "accounts using this device",
    "beneficiary_customer_fanin": "accounts paying this payee",
    "ring_size": "fraud-ring size",
}


@dataclass
class Decision:
    action: str
    risk: float
    reasons: list[str]
    rule_hits: list[str] = field(default_factory=list)
    customer_alert: str | None = None
    review_queue: bool = False


def _money(x: float) -> str:
    return f"₹{x:,.2f}"


def _signed(v: float) -> str:
    return f"+{v * 100:.0f} pp" if v >= 0 else f"{v * 100:.0f} pp"


def action_for(risk: float, sev: str | None, model_says_fraud: bool) -> str:
    """The action map, factored out so shadow policies (rules-only, model-only)
    can be evaluated on every transaction for the live comparison."""
    if sev == "block" or risk >= RISK_BLOCK:
        return "BLOCK"
    if sev == "challenge" or risk >= RISK_CHALLENGE or model_says_fraud:
        return "CHALLENGE"
    if sev == "flag" or risk >= RISK_REVIEW:
        return "REVIEW"
    return "ALLOW"


def shadow_actions(score: ScoreBreakdown, hits: list[RuleHit],
                   label_threshold: float) -> dict:
    """What each policy would decide: rules only, model only, and the two combined."""
    sev = worst_severity(hits)
    mf = score.fraud_proba >= label_threshold
    return {
        "rules_only": action_for(0.0, sev, False),
        "model_only": action_for(score.risk, None, mf),
        "full": action_for(score.risk, sev, mf),
    }


def decide(score: ScoreBreakdown, hits: list[RuleHit], txn: dict,
           label_threshold: float = 0.5) -> Decision:
    sev = worst_severity(hits)
    risk = score.risk
    model_says_fraud = score.fraud_proba >= label_threshold
    action = action_for(risk, sev, model_says_fraud)

    reasons = [h.message for h in hits]
    if not reasons or action in ("BLOCK", "CHALLENGE"):
        for name, value, contrib in score.top_features:
            if abs(contrib) < 1e-4:
                continue
            label = _PRETTY.get(name, name.replace("_", " "))
            reasons.append(f"{label}: {value:.2f} ({_signed(contrib)} calibrated-probability contrast vs reference)")
    reasons.insert(0, f"Risk {risk*100:.0f}% — calibrated fraud probability "
                      f"{score.fraud_proba*100:.0f}%, novelty {score.anomaly*100:.0f}%")

    amt, ch = _money(float(txn["amount"])), txn["channel"]
    alert = None
    if action == "BLOCK":
        alert = (f"We blocked a suspicious {amt} {ch} transaction on your account. "
                 f"If this was you, reply YES or approve it in the app.")
    elif action == "CHALLENGE":
        alert = (f"Please verify it's you: we've paused a {amt} {ch} transaction "
                 f"pending a one-time passcode.")

    return Decision(action, round(risk, 4), reasons, [h.code for h in hits],
                    alert, action in ("REVIEW", "CHALLENGE", "BLOCK"))
