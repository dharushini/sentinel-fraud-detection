"""Turn a scored case into an analyst-style narrative.

No LLM — the text is composed from the model's own counterfactual feature probes and rules
that fired, and the feature values, so it always matches the decision exactly and
costs nothing. It reads like the note a fraud analyst would leave on the case.
"""
from __future__ import annotations

# feature -> function(value) -> human clause, used for the driver / mitigator lines
_CLAUSE = {
    "amount_z": lambda v: (f"the amount is {abs(v):.1f}σ "
                           f"{'above' if v >= 0 else 'below'} this customer's usual spend"),
    "amount_to_max": lambda v: (f"it is {v:.1f}× the customer's previous largest transaction"
                                if v >= 1 else "the amount is within the customer's normal range"),
    "new_beneficiary": lambda v: "the payee has never been paid before" if v >= 1 else None,
    "new_merchant": lambda v: "the merchant is new to this customer" if v >= 1 else None,
    "new_device": lambda v: "the device is unrecognised" if v >= 1 else "the device is known",
    "new_country": lambda v: "it is the first transaction ever in this country" if v >= 1 else None,
    "is_foreign": lambda v: "it is a foreign transaction" if v >= 1 else None,
    "impossible_travel": lambda v: ("the location is physically unreachable from the "
                                    "previous transaction") if v >= 1 else None,
    "speed_kmh": lambda v: (f"implied travel speed since the last transaction is "
                            f"{v:,.0f} km/h") if v > 900 else None,
    "failed_logins_1h": lambda v: f"{int(v)} failed logins preceded it" if v >= 1 else None,
    "txn_count_5m": lambda v: f"{int(v)} transactions in the last 5 minutes" if v >= 3 else None,
    "txn_count_1h": lambda v: f"{int(v)} transactions in the last hour" if v >= 6 else None,
    "amt_sum_1h": lambda v: f"₹{v:,.0f} spent in the last hour" if v > 0 else None,
    "high_risk_mcc": lambda v: "it is a high-risk category (crypto / gift card / wire)" if v >= 1 else None,
    "is_night": lambda v: "it happened overnight" if v >= 1 else None,
    "merchant_fraud_rate": lambda v: f"the merchant has a {v*100:.0f}% historical fraud rate" if v > 0.05 else None,
    "device_fraud_rate": lambda v: f"the device has a {v*100:.0f}% historical fraud rate" if v > 0.05 else None,
    "beneficiary_fraud_rate": lambda v: f"the payee has a {v*100:.0f}% historical fraud rate" if v > 0.05 else None,
    "entity_max_fraud_rate": lambda v: (f"a merchant / device / payee on this transaction has a "
                                        f"{v*100:.0f}% fraud history") if v > 0.05 else None,
    "ring_size": lambda v: (f"the device or payee is shared across {int(v)} different "
                            f"customers (fraud-ring pattern)") if v >= 3 else None,
    "beneficiary_customer_fanin": lambda v: (f"{int(v)} different customers pay this same payee"
                                             ) if v >= 3 else None,
    "seq_regime_kl": lambda v: "the customer's behaviour pattern shifted abruptly" if v > 0.3 else None,
    "seq_repeat_5": lambda v: "the same behaviour pattern is being repeated rapidly" if v > 0.5 else None,
    "seq_surprise": lambda v: "this behaviour is unusual for the customer's history" if v > 0.5 else None,
    "seq_new_token": lambda v: "the customer has never produced this pattern of behaviour" if v >= 1 else None,
    "account_age_days": lambda v: (f"the account is well established ({int(v):,} days old)"
                                   if v > 400 else f"the account is only {int(v)} days old"),
    "distinct_countries_24h": lambda v: f"transactions in {int(v)} countries in 24 h" if v >= 3 else None,
}

_HEADLINE = {
    "BLOCK": "Blocked",
    "CHALLENGE": "Step-up verification required",
    "REVIEW": "Flagged for analyst review",
    "ALLOW": "Cleared",
}
_RECO = {
    "BLOCK": "Hold the transaction and notify the customer; open a case for confirmation.",
    "CHALLENGE": "Pause and require a one-time passcode / passkey before releasing.",
    "REVIEW": "Allow now, but queue for an analyst to look at within SLA.",
    "ALLOW": "No action — let it through.",
}


def _clause(name: str, value: float):
    fn = _CLAUSE.get(name)
    if fn is None:
        return None
    try:
        return fn(float(value))
    except Exception:
        return None


def _noun(case: dict) -> str:
    ch = case.get("channel", "")
    if ch == "transfer":
        return "transfer"
    if ch == "atm":
        return "ATM withdrawal"
    return f"{case.get('mcc', 'card')} {ch or 'purchase'}".replace("_", " ")


def narrate(case: dict) -> dict:
    action = case["action"]
    risk = case.get("risk", 0.0)
    expl = case.get("explanation") or []
    feats = case.get("features") or {}
    rules = case.get("rule_hits") or []

    # features that only ever *raise* suspicion — never present them as reassuring
    RISK_ONLY = {
        "impossible_travel", "speed_kmh", "failed_logins_1h", "new_beneficiary",
        "new_country", "high_risk_mcc", "ring_size", "beneficiary_customer_fanin",
        "seq_regime_kl", "seq_repeat_5", "seq_surprise", "seq_new_token",
        "merchant_fraud_rate", "device_fraud_rate", "beneficiary_fraud_rate",
        "entity_max_fraud_rate", "txn_count_5m", "txn_count_1h", "distinct_countries_24h",
    }
    drivers, mitigators = [], []
    for e in expl:
        c = _clause(e["feature"], e["value"])
        if not c:
            continue
        if e["contribution"] >= 0:
            drivers.append(c)
        elif e["feature"] not in RISK_ONLY:      # genuine mitigating factor
            mitigators.append(c)
    drivers, mitigators = drivers[:3], mitigators[:2]

    decisive = action == "BLOCK" and (risk >= 0.9 or any(
        r in rules for r in ("impossible_travel", "known_bad_entity",
                              "card_testing", "ato_login_then_spend")))
    headline = ("High-confidence block" if decisive
                else "Cleared — looks normal" if action == "ALLOW" and risk < 0.15
                else _HEADLINE[action])

    amt = f"₹{case.get('amount', 0):,.2f}"
    who = case.get("cust_id", "the customer")
    dest = (f" to new payee {case['beneficiary']}" if case.get("beneficiary")
            and feats.get("new_beneficiary", 0) >= 1 else
            f" to {case['beneficiary']}" if case.get("beneficiary") else
            f" at {case.get('merchant_id', 'a merchant')}")
    loc = f" in {case.get('city') or case.get('country', '')}".rstrip()

    verb = {"BLOCK": "was blocked", "CHALLENGE": "was paused for verification",
            "REVIEW": "was allowed but flagged", "ALLOW": "was allowed"}[action]

    sent = [f"This {amt} {_noun(case)}{dest}{loc} by {who} {verb}."]
    if rules and action in ("BLOCK", "CHALLENGE"):
        sent.append(case["reasons"][1] if len(case.get("reasons", [])) > 1
                    else f"A hard rule fired ({rules[0].replace('_', ' ')}).")
    if drivers:
        sent.append("Key signals: " + _join(drivers) + ".")
    if mitigators:
        sent.append("Lowering concern: " + _join(mitigators) + ".")
    if not drivers and not mitigators and action == "ALLOW":
        sent.append("Amount, merchant, location, device and timing are all "
                    "consistent with this customer's history.")

    out = {
        "headline": headline,
        "summary": " ".join(sent),
        "drivers": drivers,
        "mitigators": mitigators,
        "recommendation": _RECO[action],
    }
    if "label" in case:
        gt = (f"fraud ({case.get('scenario', 'unknown')})" if case["label"] else "legitimate")
        out["ground_truth"] = (f"Ground truth: {gt} — the model was "
                               f"{'correct' if case.get('correct') else 'wrong'}.")
    return out


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
