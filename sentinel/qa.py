"""Natural-language Q&A over a single scored case.

The rest of Sentinel is deliberately LLM-free (narrate.py composes text from
real feature values with no model call, so it's free and always consistent
with the decision). This module is the one deliberate exception: a judge or
analyst can type an open-ended question ("why was this blocked?", "would it
still block if the amount was ₹500?") and get a real, flexible answer.

To keep that honest, the LLM is never allowed to freelance. Every call is
grounded in the *exact* case dict Sentinel already produced — decision,
risk score, rule hits, SHAP-style feature attributions, raw feature values —
serialized into the prompt as the *only* source of fact, with an explicit
instruction to say so and refuse rather than guess if the question can't be
answered from that data. This is retrieval-grounded Q&A over real pipeline
output, not a chatbot with opinions about fraud.

Requires GROQ_API_KEY in the environment. If it's not set, /qa returns a
clear 503 rather than silently degrading to a canned response — a judge
asking "does this actually call an LLM?" deserves a true answer.
"""
from __future__ import annotations

import os

_SYSTEM_PROMPT = """You are a fraud-analyst assistant embedded in Sentinel, a real-time \
transaction fraud detection system. You answer questions about ONE specific, already-scored \
transaction. You are given that transaction's real data below: the decision Sentinel made, \
its risk score, which deterministic rules fired, the top feature attributions (how much each \
factor pushed the risk score up or down), and the raw feature values.

Rules you must follow:
1. Answer ONLY from the data given below. Never invent a fact, a number, or a reason that \
isn't in this data.
2. If the question asks about something this data doesn't cover (e.g. this customer's full \
transaction history, other customers, or anything outside this one case), say plainly that \
you don't have that information rather than guessing.
3. If asked a hypothetical ("what if the amount was higher?"), you may reason qualitatively \
about which feature would change and the likely direction of effect, but say clearly that \
this is a qualitative estimate, not a re-score — you are not able to actually re-run the model.
4. Keep answers short: 2-4 sentences, plain English, no jargon unless the question uses it first.
5. Never claim certainty you don't have. Sentinel's decisions are probabilistic, not proof of \
fraud or innocence.
"""


def _format_case_context(case: dict) -> str:
    lines = [
        f"Decision: {case.get('action')}",
        f"Risk score: {case.get('risk')} (0-1 scale, calibrated fraud probability {case.get('fraud_proba')})",
        f"Anomaly / novelty score: {case.get('anomaly')}",
        f"Amount: ${case.get('amount')}",
        f"Customer: {case.get('cust_id')}",
        f"Merchant / payee: {case.get('merchant_id')} / {case.get('beneficiary') or '(none)'}",
        f"Location: {case.get('city')}, {case.get('country')}",
        f"Channel / category: {case.get('channel')} / {case.get('mcc')}",
    ]
    hits = case.get("rule_hits") or []
    if hits:
        lines.append("Deterministic rules that fired: " + "; ".join(str(h) for h in hits))
    else:
        lines.append("Deterministic rules that fired: none")

    explanation = case.get("explanation") or []
    if explanation:
        lines.append("Top feature attributions (feature: value, contribution to risk score):")
        for e in explanation[:8]:
            lines.append(f"  - {e['feature']}: value={e['value']}, contribution={e['contribution']:+.4f}")

    reasons = case.get("reasons") or []
    if reasons:
        lines.append("Plain-English reasons already generated: " + "; ".join(reasons))

    online_adj = case.get("online_adjustment")
    if online_adj:
        lines.append(f"Online-learning correction applied to this score: {online_adj:+.4f}")

    cf = case.get("counterfactual")
    if cf:
        lines.append(f"Counterfactual note: {cf}")

    return "\n".join(lines)


class QAError(Exception):
    """Raised when the Q&A feature can't run (no key, upstream failure)."""


def answer_question(case: dict, question: str) -> dict:
    """Ask Groq a question grounded in one case's real data. Returns
    {"answer": str, "model": str}. Raises QAError if unavailable."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise QAError(
            "GROQ_API_KEY is not set. This feature calls a real LLM (Groq) "
            "grounded in the case data — set the environment variable to enable it."
        )
    try:
        from groq import Groq
    except ImportError as e:
        raise QAError("the 'groq' package isn't installed (pip install groq)") from e

    context = _format_case_context(case)
    model_name = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
    client = Groq(api_key=api_key)
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Transaction data:\n{context}\n\nQuestion: {question}"},
            ],
            temperature=0.2,
            max_tokens=300,
        )
    except Exception as e:  # network / auth / rate-limit — surface honestly, don't fabricate
        raise QAError(f"the Groq API call failed: {e}") from e

    answer = resp.choices[0].message.content.strip()
    return {"answer": answer, "model": model_name}
