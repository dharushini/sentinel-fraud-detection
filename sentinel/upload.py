"""Bulk transaction upload: validate a CSV a bank analyst exports from their
own system and turn every valid row into a Sentinel transaction event.

Design rules:

* **Never silently guess.** A row that can't be scored as written (bad amount,
  unknown channel, unparseable date, impossible age) is rejected with its row
  number and the exact reason; the valid rows are still scored.
* **Forgiving headers.** Column names are matched case-insensitively and a few
  common spellings are accepted (amount / amt / txn_amount, ...), so an
  export doesn't need renaming.
* **Timestamps matter.** Velocity features ("5 transactions in 5 minutes")
  only mean something with real times. If the file has no timestamp column,
  rows are spread evenly over the previous 24 hours in file order and the
  response carries a warning saying so, rather than stacking them all at one
  instant (which would look exactly like a card-testing burst).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

MAX_ROWS = 2000          # ~8 ms/row through the full pipeline -> well inside a 60 s serverless request
MAX_BYTES = 1_000_000

CHANNELS = {"pos", "online", "atm", "transfer"}
CHANNEL_ALIASES = {
    "in-store": "pos", "instore": "pos", "store": "pos", "card": "pos", "pos": "pos",
    "online": "online", "web": "online", "ecommerce": "online", "e-commerce": "online", "app": "online",
    "atm": "atm", "cash": "atm", "withdrawal": "atm",
    "transfer": "transfer", "upi": "transfer", "neft": "transfer", "imps": "transfer",
    "rtgs": "transfer", "wire": "transfer", "p2p": "transfer",
    "p2m": "online", "billpay": "online", "recharge": "online",
}
CATEGORIES = {
    "grocery", "restaurant", "transport", "utilities", "retail", "entertainment",
    "travel", "electronics", "crypto", "gift_card", "wire_transfer", "upi_transfer",
    "bank_transfer", "atm_withdrawal",
}

# canonical field -> accepted header spellings (lower-cased, spaces -> _)
FIELDS = {
    "cust_id": ("cust_id", "customer_id", "customer", "account_id", "account", "payer", "payer_vpa"),
    "amount": ("amount", "amt", "txn_amount", "transaction_amount", "value", "amount_inr"),
    "ts": ("ts", "timestamp", "time", "datetime", "date", "txn_time", "transaction_time"),
    "merchant_id": ("merchant_id", "merchant", "merchant_name", "payee", "payee_vpa"),
    "mcc": ("mcc", "category", "merchant_category"),
    "channel": ("channel", "mode", "payment_mode", "type"),
    "city": ("city", "location"),
    "country": ("country", "country_code"),
    "device_id": ("device_id", "device", "device_fingerprint"),
    "beneficiary": ("beneficiary", "beneficiary_id", "beneficiary_account"),
    "cust_age": ("cust_age", "age", "customer_age"),
    "label": ("label", "is_fraud", "fraud", "isfraud"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "long", "longitude"),
}

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
               "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M",
               "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y")

TEMPLATE_CSV = (
    "cust_id,amount,timestamp,merchant,category,channel,city,country,device_id,beneficiary,cust_age,is_fraud\n"
    "acct_1001,1250.00,2026-09-01 10:15:00,Reliance Fresh,grocery,pos,Mumbai,IN,phone_1001,,34,0\n"
    "acct_1001,899.00,2026-09-01 19:40:00,Swiggy,restaurant,online,Mumbai,IN,phone_1001,,34,0\n"
    "acct_2002,45000.00,2026-09-01 02:05:00,new_payee_77,wire_transfer,transfer,Delhi,IN,laptop_x9,mule_77,68,1\n"
    "acct_3003,320.50,2026-09-02 13:00:00,Uber,transport,online,Bengaluru,IN,phone_3003,,22,\n"
)

# city -> (lat, lon) for the common Indian cities, so geo features aren't all
# pinned to one point when a file has a city but no coordinates
CITY_COORDS = {
    "mumbai": (19.08, 72.88), "delhi": (28.61, 77.21), "new delhi": (28.61, 77.21),
    "bengaluru": (12.97, 77.59), "bangalore": (12.97, 77.59), "chennai": (13.08, 80.27),
    "hyderabad": (17.39, 78.49), "kolkata": (22.57, 88.36), "pune": (18.52, 73.86),
    "ahmedabad": (23.02, 72.57), "jaipur": (26.91, 75.79), "lucknow": (26.85, 80.95),
    "kochi": (9.93, 76.27), "chandigarh": (30.73, 76.78), "surat": (21.17, 72.83),
}
DEFAULT_COORDS = CITY_COORDS["mumbai"]


def _norm(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "_").replace("-", "_")


def _map_headers(headers: list[str]) -> dict[str, str]:
    """canonical field -> the actual header in this file."""
    normed = {_norm(h): h for h in headers if h is not None}
    out = {}
    for field, aliases in FIELDS.items():
        for a in aliases:
            if a in normed:
                out[field] = normed[a]
                break
    return out


def _parse_ts(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def _parse_label(raw: str) -> int | None:
    v = raw.strip().lower()
    if v in ("", "na", "n/a", "unknown", "?"):
        return None
    if v in ("1", "1.0", "true", "yes", "y", "fraud"):
        return 1
    if v in ("0", "0.0", "false", "no", "n", "legit", "genuine"):
        return 0
    raise ValueError(f"is_fraud must be 0/1, yes/no or true/false (got '{raw}')")


def parse_transactions_csv(text: str, now: datetime | None = None) -> dict:
    """Validate a CSV and return {events, errors, warnings, rows, has_labels}.

    `errors` is a list of {row, error} with 1-based data-row numbers as a
    spreadsheet shows them (header = row 1, first data row = row 2)."""
    now = now or datetime.utcnow()
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError(f"file is larger than {MAX_BYTES // 1_000_000} MB — split it into smaller files")
    text = text.lstrip("﻿")                     # Excel's UTF-8 BOM
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("the file is empty or has no header row")
    cols = _map_headers(reader.fieldnames)
    missing = [f for f in ("cust_id", "amount") if f not in cols]
    if missing:
        raise ValueError("missing required column(s): " + ", ".join(missing) +
                         f" — found: {', '.join(h for h in reader.fieldnames if h)}")

    rows = list(reader)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"{len(rows):,} rows — the limit is {MAX_ROWS:,} per upload; split the file")
    if not rows:
        raise ValueError("the file has a header but no data rows")

    get = lambda r, f: (r.get(cols[f]) or "").strip() if f in cols else ""
    events, errors, warnings = [], [], []
    has_ts = "ts" in cols
    if not has_ts:
        warnings.append("No timestamp column: rows were spread evenly over the last 24 hours in file "
                        "order. Velocity signals (e.g. many payments in a few minutes) are only "
                        "meaningful with real transaction times.")
    labelled = 0

    for i, r in enumerate(rows):
        rownum = i + 2
        try:
            cust = get(r, "cust_id")
            if not cust:
                raise ValueError("cust_id is empty")
            amt_raw = get(r, "amount").replace(",", "").replace("₹", "").replace("INR", "").strip()
            try:
                amount = float(amt_raw)
            except ValueError:
                raise ValueError(f"amount '{get(r, 'amount')}' is not a number")
            if not amount > 0:
                raise ValueError(f"amount must be greater than 0 (got {amt_raw})")

            if has_ts:
                ts = _parse_ts(get(r, "ts"))
                if ts is None:
                    raise ValueError(f"timestamp '{get(r, 'ts')}' not recognised — use YYYY-MM-DD HH:MM:SS")
            else:
                ts = now - timedelta(hours=24) + timedelta(seconds=86400 * i / max(len(rows), 1))

            ch_raw = get(r, "channel").lower()
            channel = CHANNEL_ALIASES.get(ch_raw, ch_raw) if ch_raw else "online"
            if channel not in CHANNELS:
                raise ValueError(f"channel '{get(r, 'channel')}' must be one of pos, online, atm, transfer")

            mcc = (get(r, "mcc").lower().replace(" ", "_") or "retail")
            if mcc not in CATEGORIES:
                raise ValueError(f"category '{get(r, 'mcc')}' is not recognised — use one of: "
                                 + ", ".join(sorted(CATEGORIES)))

            age_raw = get(r, "cust_age")
            age = None
            if age_raw:
                try:
                    age = int(float(age_raw))
                except ValueError:
                    raise ValueError(f"age '{age_raw}' is not a number")
                if not 18 <= age <= 120:
                    raise ValueError(f"age {age} is outside 18-120")

            label = _parse_label(get(r, "label")) if "label" in cols else None

            city = get(r, "city")
            lat_raw, lon_raw = get(r, "lat"), get(r, "lon")
            if lat_raw and lon_raw:
                try:
                    lat, lon = float(lat_raw), float(lon_raw)
                except ValueError:
                    raise ValueError("latitude/longitude must be numbers")
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("latitude/longitude out of range")
            else:
                lat, lon = CITY_COORDS.get(city.lower(), DEFAULT_COORDS)

            beneficiary = get(r, "beneficiary")
            ev = {
                "type": "txn", "ts": ts, "cust_id": cust, "amount": round(amount, 2),
                "mcc": mcc, "channel": channel,
                "merchant_id": get(r, "merchant_id") or f"merchant_{mcc}",
                "beneficiary": beneficiary,
                "country": (get(r, "country") or "IN").upper(), "city": city,
                "lat": lat, "lon": lon,
                "device_id": get(r, "device_id") or f"dev_{cust}",
                "card_bin": "?",
                "label": label if label is not None else 0,
                "scenario": "upload",
            }
            if age is not None:
                ev["cust_age"] = age
            if label is not None:
                labelled += 1
                ev["_labelled"] = True
            events.append(ev)
        except ValueError as exc:
            errors.append({"row": rownum, "error": str(exc)})

    events.sort(key=lambda e: e["ts"])
    return {"events": events, "errors": errors, "warnings": warnings,
            "rows": len(rows), "has_labels": labelled > 0, "labelled": labelled}
