"""Deterministic tripwires.

The ML score is the brain; these rules are the reflexes. They exist so a
blatant attack is stopped instantly and *explainably*, even if the model is
uncertain, and so a compliance officer can point at a written policy.

Each rule returns a ``RuleHit`` with a severity that the decision engine maps
to an action:  block > challenge > flag.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import (
    ATO_FAILED_LOGINS,
    CARD_TESTING_TXNS_5M, CARD_TESTING_MAX_AMOUNT, VELOCITY_MIN_HISTORY, VELOCITY_PERSONAL_MULT,
    ENTITY_FRAUD_RATE_BLOCK,
    HIGH_RISK_MCC,
    RING_SIZE_CHALLENGE,
    VELOCITY_TXNS_1H,
)


@dataclass(frozen=True)
class RuleHit:
    code: str
    severity: str      # "block" | "challenge" | "flag"
    message: str


def _faster_than_usual(f: dict, window: str, baseline: dict | None) -> bool:
    """Is this burst unusual for THIS customer? Absolute velocity thresholds
    alone challenge every transaction of a customer who is always busy (a
    shopkeeper, a heavy UPI user). Once a customer has enough history, a
    velocity rule fires only if the current count clearly exceeds their own
    established busiest spell (ProfileState.velocity_baseline); for new
    customers, or when no baseline is supplied, the absolute threshold stands."""
    if not baseline or baseline.get("n", 0.0) < VELOCITY_MIN_HISTORY:
        return True
    count = f["txn_count_5m"] if window == "5m" else f["txn_count_1h"]
    peak = baseline["peak_5m"] if window == "5m" else baseline["peak_1h"]
    return count > VELOCITY_PERSONAL_MULT * peak


def evaluate_rules(feat: dict, txn: dict, baseline: dict | None = None) -> list[RuleHit]:
    hits: list[RuleHit] = []
    f = feat  # shorthand

    # ------------------------------------------------------------------
    # Absolute-amount tripwires. These work even for a brand-new customer
    # with no history, where amount_z is always 0 and the relative rules
    # below can never fire.
    # ------------------------------------------------------------------
    amt = float(txn.get("amount", 0.0))
    cold = (not baseline) or baseline.get("n", 0.0) < VELOCITY_MIN_HISTORY

    if amt >= 1_000_000:
        hits.append(RuleHit(
            "amount_hard_limit", "block",
            f"Amount ₹{amt:,.0f} exceeds the ₹10,00,000 single-transaction hard limit",
        ))
    elif amt >= 200_000 and f["new_device"] >= 1.0 and cold:
        hits.append(RuleHit(
            "large_new_device_no_history", "block",
            f"₹{amt:,.0f} from an unrecognised device on an account with no history",
        ))
    elif amt >= 200_000:
        hits.append(RuleHit(
            "amount_high", "challenge",
            f"Amount ₹{amt:,.0f} is above the ₹2,00,000 verification threshold",
        ))
    elif f["new_device"] >= 1.0 and amt >= 50_000:
        hits.append(RuleHit(
            "new_device_high_amount", "challenge",
            f"₹{amt:,.0f} from a device this customer has never used",
        ))
    elif cold and amt >= 25_000:
        hits.append(RuleHit(
            "first_time_large", "flag",
            f"First-time customer with no history making a ₹{amt:,.0f} payment",
        ))

    if f["impossible_travel"] >= 1.0:
        hits.append(RuleHit(
            "impossible_travel", "block",
            f"Impossible travel: {f['dist_from_last_km']:.0f} km in "
            f"{f['secs_since_last']/3600:.1f} h (~{f['speed_kmh']:.0f} km/h) "
            f"since the last transaction",
        ))

    if f["txn_count_5m"] >= CARD_TESTING_TXNS_5M and _faster_than_usual(f, "5m", baseline):
        # Card testing = a burst of TINY probe charges at merchants the
        # customer has never used, checking whether a stolen card works.
        # Ordinary bursts (splitting a bill, retrying a UPI payment, a small
        # shop's busy hour) are fast but not that pattern; hard-blocking them
        # declined most genuine customers on UPI traffic. They get step-up
        # verification instead — and only when faster than THIS customer's
        # own usual pace (see _faster_than_usual).
        if (float(txn.get("amount", 0.0)) <= CARD_TESTING_MAX_AMOUNT
                and f.get("new_merchant", 1.0) >= 1.0):
            hits.append(RuleHit(
                "card_testing", "block",
                f"Card-testing pattern: {int(f['txn_count_5m'])} small payments to new merchants in 5 minutes",
            ))
        else:
            hits.append(RuleHit(
                "rapid_burst", "challenge",
                f"Rapid burst: {int(f['txn_count_5m'])} transactions in 5 minutes — faster than this customer's usual pace",
            ))

    if f["failed_logins_1h"] >= ATO_FAILED_LOGINS and f["amount_to_max"] > 1.0:
        hits.append(RuleHit(
            "ato_login_then_spend", "block",
            f"High-value {txn['channel']} transaction right after "
            f"{int(f['failed_logins_1h'])} failed logins — possible account takeover",
        ))

    if f["new_country"] >= 1.0 and f["amount_z"] > 4.0:
        hits.append(RuleHit(
            "new_country_large_amount", "block",
            f"First-ever transaction in {txn['country']} at "
            f"{f['amount_z']:.1f}σ above this customer's normal spend",
        ))

    if (f["new_device"] >= 1.0 and f["channel_online"] >= 1.0
            and f["amount"] > 5000 and f["failed_logins_1h"] >= 1.0):
        hits.append(RuleHit(
            "new_device_online_spend", "challenge",
            "Large online purchase from an unrecognised device after a failed login",
        ))

    if f["high_risk_mcc"] >= 1.0 and f["new_merchant"] >= 1.0 and f["amount_z"] > 2.0:
        hits.append(RuleHit(
            "high_risk_new_merchant", "challenge",
            f"Large payment to a new {txn['mcc'].replace('_', ' ')} merchant "
            f"({f['amount_z']:.1f}σ above normal)",
        ))

    if f["txn_count_1h"] >= VELOCITY_TXNS_1H and _faster_than_usual(f, "1h", baseline):
        hits.append(RuleHit(
            "velocity_1h", "challenge",
            f"Unusual velocity: {int(f['txn_count_1h'])} transactions in the past hour",
        ))

    if f.get("entity_max_fraud_rate", 0.0) >= ENTITY_FRAUD_RATE_BLOCK:
        hits.append(RuleHit(
            "known_bad_entity", "block",
            f"Merchant / device / payee on this transaction has a "
            f"{f['entity_max_fraud_rate']*100:.0f}% historical fraud rate",
        ))

    if f.get("ring_size", 0.0) >= RING_SIZE_CHALLENGE:
        hits.append(RuleHit(
            "fraud_ring", "challenge",
            f"Device or beneficiary is shared across {int(f['ring_size'])} "
            f"different customers — likely a fraud ring / mule account",
        ))

    if (f.get("new_beneficiary", 0.0) >= 1.0 and f["channel_transfer"] >= 1.0
            and f["amount_z"] > 1.5):
        hits.append(RuleHit(
            "new_payee_large_transfer", "flag",
            "Large transfer to a payee this customer has never sent to before",
        ))

    if (f["amount_z"] > 4.0 and f["amount"] > 1500
            and (f["new_merchant"] >= 1.0 or f["is_foreign"] >= 1.0 or f["high_risk_mcc"] >= 1.0)
            and not any(h.code == "new_country_large_amount" for h in hits)):
        hits.append(RuleHit(
            "amount_spike", "flag",
            f"Amount is {f['amount_z']:.1f}σ above this customer's usual spend, "
            f"at a new/foreign/high-risk merchant",
        ))

    if f["is_foreign"] >= 1.0 and f["new_country"] >= 1.0 and f["channel_pos"] >= 1.0:
        hits.append(RuleHit(
            "foreign_pos_first_visit", "flag",
            f"In-person spend in {txn['country']} for the first time",
        ))

    return hits


def worst_severity(hits: list[RuleHit]) -> str | None:
    order = {"flag": 1, "challenge": 2, "block": 3}
    if not hits:
        return None
    return max(hits, key=lambda h: order[h.severity]).severity
