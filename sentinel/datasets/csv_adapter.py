"""Adapters that turn real public fraud datasets into Sentinel events.

    SENTINEL_DATA=/path/creditcard.csv          SENTINEL_CSV_SCHEMA=ulb
    SENTINEL_DATA=/path/fraudTrain.csv          SENTINEL_CSV_SCHEMA=sparkov
    SENTINEL_DATA=/path/ieee_train.csv          SENTINEL_CSV_SCHEMA=ieee
    SENTINEL_DATA=/path/upi_fraud.csv           SENTINEL_CSV_SCHEMA=upi
    SENTINEL_DATA=/path/paysim.csv              SENTINEL_CSV_SCHEMA=paysim

* **sparkov** — Kaggle "Credit Card Transactions Fraud Detection Dataset".
  Full mapping: has timestamp, card number, merchant, category, amount, geo.
* **ieee**   — Kaggle IEEE-CIS Fraud Detection (transaction file). Partial:
  no geo; (card1, addr1) is used as the customer proxy, DeviceInfo as device,
  P_emaildomain as a beneficiary-like entity.
* **ulb**    — Kaggle ULB "creditcard.csv" (V1..V28 PCA features). No entities,
  so events carry a ``raw_features`` dict and the trainer skips feature
  engineering and learns on the raw columns directly.
* **upi**    — Real Indian UPI/Razorpay-style transaction export (columns:
  timestamp, amount, currency=INR, upi_app, bank, device_fingerprint,
  is_suspicious, fraud_reasons, ...). No geo; device_fingerprint (stable,
  recurring per payer) is used as the customer proxy the same way ieee uses
  (card1, addr1), upi_app as the channel-equivalent merchant, and bank folded
  into the merchant id so a "known bad bank/app pairing" is learnable the same
  way a known-bad merchant is elsewhere. Country fixed to IN.
* **india_bank** — an Indian retail-banking dataset (transactions + customers
  with age + cards + merchants; INR, Indian states/cities, RuPay/UPI). Each
  event carries the customer's real `cust_age` from the customer table. See
  `_india_bank` for what is and isn't loaded.
* **paysim** — Kaggle "Synthetic Financial Datasets For Fraud Detection"
  (PaySim, Lopez-Rojas et al. 2016): a mobile-money simulator, columns
  step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,
  oldbalanceDest,newbalanceDest,isFraud,isFlaggedFraud. ``nameOrig`` is the
  customer, ``type`` maps to a channel/mcc (CASH_IN/CASH_OUT/TRANSFER/
  PAYMENT/DEBIT), ``step`` (1 simulated hour each) becomes the timestamp.
  PaySim's ``amount`` is an abstract simulation unit, not a real currency
  figure (it commonly runs into the hundreds of thousands per row), so it is
  log-compressed down into the same realistic INR band the rest of Sentinel
  uses (roughly ₹50-₹20,000+) rather than relabelled as-is — see
  ``_paysim_amount_to_inr``. Country fixed to IN; no geo in the source data.

Uses only the standard library so it streams large files without pandas.
"""
from __future__ import annotations

import csv
import hashlib
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

_EPOCH = datetime(2020, 1, 1)


def _f(row: dict, *keys, default=0.0) -> float:
    for k in keys:
        v = row.get(k)
        if v not in (None, "", "NaN", "nan"):
            try:
                return float(v)
            except ValueError:
                pass
    return default


def _hash_bin(value: str) -> str:
    return hashlib.sha1(str(value).encode()).hexdigest()[:6]


def load_csv_events(path: str, schema: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    fn = {"sparkov": _sparkov, "ieee": _ieee, "ulb": _ulb, "upi": _upi,
          "paysim": _paysim, "india_bank": _india_bank}.get(schema)
    if fn is None:
        raise ValueError(f"unknown CSV schema '{schema}' (sparkov | ieee | ulb | upi | paysim | india_bank)")
    with p.open(newline="") as fh:
        reader = csv.DictReader(fh)
        events = list(fn(reader, p.parent) if schema == "india_bank" else fn(reader))
    events.sort(key=lambda e: e["ts"])
    return events


# --------------------------------------------------------------------------- #
def _sparkov(reader: csv.DictReader) -> Iterator[dict]:
    for r in reader:
        try:
            ts = datetime.strptime(r["trans_date_trans_time"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            ts = _EPOCH + timedelta(seconds=_f(r, "unix_time"))
        cc = r.get("cc_num", "unknown")
        amt = _f(r, "amt")
        cat = (r.get("category") or "retail").replace("_", "")
        channel = "online" if "net" in cat or "online" in cat else "pos"
        mcc = _norm_mcc(cat)
        yield {
            "type": "txn", "ts": ts, "cust_id": f"cc_{cc}",
            "amount": amt, "mcc": mcc, "channel": channel,
            "merchant_id": (r.get("merchant") or "m").replace("fraud_", ""),
            "beneficiary": "", "country": "US",
            "city": r.get("city", ""),
            "lat": _f(r, "lat"), "lon": _f(r, "long"),
            "device_id": f"card_{cc}", "card_bin": str(cc)[:6],
            "label": int(_f(r, "is_fraud")),
        }


def _ieee(reader: csv.DictReader) -> Iterator[dict]:
    for r in reader:
        ts = _EPOCH + timedelta(seconds=_f(r, "TransactionDT"))
        cust = f"{r.get('card1','?')}_{r.get('addr1','?')}"
        pcd = (r.get("ProductCD") or "W").upper()
        channel = {"W": "pos", "C": "online", "R": "online",
                   "H": "online", "S": "transfer"}.get(pcd, "online")
        yield {
            "type": "txn", "ts": ts, "cust_id": f"ic_{cust}",
            "amount": _f(r, "TransactionAmt"),
            "mcc": f"pcd_{pcd.lower()}", "channel": channel,
            "merchant_id": f"{r.get('card1','?')}_{pcd}",
            "beneficiary": (r.get("P_emaildomain") or "").strip(),
            "country": "US", "city": str(r.get("addr1", "")),
            "lat": 0.0, "lon": 0.0,
            "device_id": (r.get("DeviceInfo") or f"card_{r.get('card1','?')}").strip(),
            "card_bin": str(r.get("card1", "?")),
            "label": int(_f(r, "isFraud")),
        }


def _ulb(reader: csv.DictReader) -> Iterator[dict]:
    for r in reader:
        t = _f(r, "Time")
        raw = {f"V{i}": _f(r, f"V{i}") for i in range(1, 29)}
        raw["Amount"] = _f(r, "Amount")
        yield {
            "type": "txn", "ts": _EPOCH + timedelta(seconds=t),
            "cust_id": "ulb", "amount": _f(r, "Amount"),
            "mcc": "retail", "channel": "online", "merchant_id": "ulb",
            "beneficiary": "", "country": "US", "city": "",
            "lat": 0.0, "lon": 0.0, "device_id": "ulb", "card_bin": "ulb",
            "label": int(_f(r, "Class")), "raw_features": raw,
        }


def _upi(reader: csv.DictReader) -> Iterator[dict]:
    for r in reader:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except (KeyError, ValueError):
            continue
        device = (r.get("device_fingerprint") or "unknown").strip()
        bank = (r.get("bank") or "bank").strip()
        app = (r.get("upi_app") or "upi").strip()
        # a failed/declined attempt never redefines "normal" behaviour, same
        # principle ProfileState.update() already applies bank-side; keep
        # only settled transactions so profiles aren't built on noise.
        if (r.get("status") or "").strip().lower() != "success":
            continue
        yield {
            "type": "txn", "ts": ts, "cust_id": f"upi_{device}",
            "amount": _f(r, "amount"),
            "mcc": "upi_transfer", "channel": "transfer",
            "merchant_id": f"{bank}_{app}",
            "beneficiary": "", "country": "IN", "city": "",
            "lat": 0.0, "lon": 0.0,
            "device_id": device, "card_bin": _hash_bin(bank),
            "label": 1 if str(r.get("is_suspicious", "")).strip().lower() == "true" else 0,
        }


def _paysim_amount_to_inr(raw: float) -> float:
    """Compress PaySim's abstract simulation-unit amount into Sentinel's
    realistic INR range (~50-20,000+) with a log transform, preserving
    relative ordering (a bigger PaySim amount stays a bigger INR amount)
    without pretending the raw number is already a rupee figure."""
    if raw <= 0:
        return 1.0
    # log1p(raw) for real PaySim data (amounts commonly 1e2-1e7) spans
    # roughly 5-16; rescale that span onto log(50)..log(20000), i.e. an INR
    # amount realistically in Sentinel's demo range.
    lo_in, hi_in = 5.0, 16.0
    lo_out, hi_out = math.log(50.0), math.log(20000.0)
    t = (math.log1p(raw) - lo_in) / (hi_in - lo_in)
    t = min(1.2, max(-0.2, t))   # allow slight overshoot instead of a hard clip
    return round(math.exp(lo_out + t * (hi_out - lo_out)), 2)


def _paysim(reader: csv.DictReader) -> Iterator[dict]:
    _TYPE_MAP = {
        "CASH_IN": ("bank_transfer", "transfer"),
        "CASH_OUT": ("atm_withdrawal", "atm"),
        "DEBIT": ("retail", "pos"),
        "PAYMENT": ("retail", "online"),
        "TRANSFER": ("wire_transfer", "transfer"),
    }
    for r in reader:
        try:
            step = int(_f(r, "step"))
        except (TypeError, ValueError):
            continue
        ts = _EPOCH + timedelta(hours=step)
        cust = (r.get("nameOrig") or "unknown").strip()
        dest = (r.get("nameDest") or "").strip()
        kind = (r.get("type") or "PAYMENT").strip().upper()
        mcc, channel = _TYPE_MAP.get(kind, ("retail", "online"))
        raw_amt = _f(r, "amount")
        yield {
            "type": "txn", "ts": ts, "cust_id": f"ps_{cust}",
            "amount": _paysim_amount_to_inr(raw_amt),
            "mcc": mcc, "channel": channel,
            "merchant_id": dest or f"ps_merchant_{kind.lower()}",
            "beneficiary": dest if channel == "transfer" else "",
            "country": "IN", "city": "",
            "lat": 0.0, "lon": 0.0,
            "device_id": f"ps_dev_{cust}", "card_bin": _hash_bin(cust),
            "label": int(_f(r, "isFraud")),
        }


# ---------------------------------------------------------------------------
# india_bank: an Indian retail-banking dataset in four tables — transactions,
# customers (with AGE), cards and merchants. Point SENTINEL_DATA / the replay
# path at the transactions file; the customer table is found beside it.
_IB_MCC = {
    "Grocery": "grocery", "Food Delivery": "restaurant", "Fuel": "transport",
    "Utilities": "utilities", "Hospital": "utilities", "Education": "utilities",
    "Financial Services": "utilities", "Fashion": "retail", "Pharmacy": "retail",
    "Ecommerce": "retail", "Entertainment": "entertainment", "Electronics": "electronics",
    "Hotel": "travel", "Travel": "travel", "Airline": "travel",
}
_IB_CUSTOMER_FILES = ("Cusmtomer_data.csv", "Customer_data.csv", "customers.csv")


def _india_bank(reader: csv.DictReader, folder: Path) -> Iterator[dict]:
    """Rows -> events, with each customer's real age from the customer table.

    * Only `Transaction_Status == Successful` rows are loaded. A decline is the
      bank's existing system's verdict (59.6 % of declines are labelled fraud
      in this data) — scoring on it would leak the answer, and a declined
      attempt never redefines "normal" behaviour.
    * Card status (expired / blocked / lost) is checked by core banking before
      a fraud model sees the transaction, so it is not a model input here.
    * No coordinates in the source: geo features are neutral (lat/lon 0) and
      `Is_International` marks the transaction as foreign."""
    ages: dict[str, int] = {}
    for name in _IB_CUSTOMER_FILES:
        f = folder / name
        if f.exists():
            with f.open(newline="") as fh:
                for r in csv.DictReader(fh):
                    try:
                        ages[r["Customer_ID"]] = int(float(r["Age"]))
                    except (KeyError, ValueError):
                        pass
            break
    for r in reader:
        if (r.get("Transaction_Status") or "").strip() != "Successful":
            continue
        try:
            ts = datetime.strptime(f"{r['Transaction_Date']} {r['Transaction_Time']}", "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
        cust = (r.get("Customer_ID") or "").strip()
        method = (r.get("Payment_Method") or "").strip()
        ch_raw = (r.get("Transaction_Channel") or "").strip()
        channel = ("atm" if ch_raw == "ATM" else "pos" if ch_raw == "POS"
                   else "transfer" if method in ("UPI", "Net Banking") else "online")
        foreign = str(r.get("Is_International", "0")).strip() == "1"
        ev = {
            "type": "txn", "ts": ts, "cust_id": f"ib_{cust}",
            "amount": _f(r, "Transaction_Amount"),
            "mcc": _IB_MCC.get((r.get("Merchant_Category") or "").strip(), "retail"),
            "channel": channel,
            "merchant_id": (r.get("Merchant_ID") or "ib_merchant").strip(),
            "beneficiary": "",
            "country": "XX" if foreign else "IN",
            "city": (r.get("Merchant_City") or "").strip(),
            "lat": 0.0, "lon": 0.0,
            "device_id": f"ib_{cust}_{(r.get('Device_Type') or 'device').strip().replace(' ', '_')}",
            "card_bin": (r.get("Card_ID") or "?").strip(),
            "label": int(_f(r, "Fraud_Flag")),
        }
        if cust in ages:
            ev["cust_age"] = ages[cust]
        yield ev


_MCC_MAP = {
    "grocerypos": "grocery", "grocerynet": "grocery", "shoppingpos": "retail",
    "shoppingnet": "retail", "gasttransport": "transport", "misc_net": "retail",
    "miscpos": "retail", "entertainment": "entertainment", "food_dining": "restaurant",
    "personal_care": "retail", "health_fitness": "retail", "travel": "travel",
    "kids_pets": "retail", "home": "retail",
}


def _norm_mcc(cat: str) -> str:
    c = cat.lower().replace(" ", "").replace("-", "")
    return _MCC_MAP.get(c, "retail")
