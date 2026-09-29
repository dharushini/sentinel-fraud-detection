"""Fetch a **real, public** fraud dataset — no account, no key, no cost.

    python -m sentinel.datasets.fetch ulb

`ulb`  — the ULB / MLG "Credit Card Fraud Detection" dataset (Dal Pozzolo et al.,
2015): **284,807 real** European card‑holder transactions over two days in
September 2013, **492 real frauds (0.172 %)**. Features are PCA components
V1..V28 (anonymised for privacy) plus Time and Amount. Hosted on OpenML
(dataset 1597, CC‑BY) and downloaded here directly over HTTPS.

The file is cached under ``data/`` (git‑ignored) and written as CSV so the
existing ``csv_adapter`` (`SENTINEL_CSV_SCHEMA=ulb`) can consume it.
"""
from __future__ import annotations

import csv
import sys
import urllib.request
from pathlib import Path

from .. import config

SOURCES = {
    "ulb": {
        "arff": "https://openml.org/data/v1/download/1673544/creditcard.arff",
        "out": config.ROOT.parent / "data" / "creditcard.csv",
        "cite": ("Dal Pozzolo, Caelen, Johnson & Bontempi (2015), "
                 "'Calibrating Probability with Undersampling for Unbalanced "
                 "Classification', IEEE SSCI. OpenML dataset 1597 (CC-BY)."),
    },
}


def _arff_to_csv(text: str, out: Path) -> tuple[int, int]:
    cols: list[str] = []
    rows: list[list[str]] = []
    in_data = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        low = s.lower()
        if low.startswith("@attribute"):
            cols.append(s.split()[1].strip("'\""))
        elif low.startswith("@data"):
            in_data = True
        elif in_data:
            rows.append([c.strip().strip("'\"") for c in s.split(",")])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([c.strip("'\"") for c in cols])
        w.writerows(rows)
    # ULB labels come through as "'0'"/"'1'" — normalise the Class column
    return len(rows), sum(1 for r in rows if r and r[-1].strip("'\" ") == "1")


def fetch(name: str = "ulb") -> Path:
    if name not in SOURCES:
        raise SystemExit(f"unknown source '{name}'. options: {list(SOURCES)}")
    src = SOURCES[name]
    out: Path = src["out"]
    if out.exists():
        print(f"[fetch] cached: {out}  ({out.stat().st_size/1e6:.1f} MB)")
        return out
    print(f"[fetch] downloading real dataset '{name}' from OpenML ...")
    print(f"        cite: {src['cite']}")
    with urllib.request.urlopen(src["arff"], timeout=120) as r:
        text = r.read().decode("utf-8", "replace")
    n, fraud = _arff_to_csv(text, out)
    print(f"[fetch] wrote {out}  —  {n:,} transactions, {fraud:,} fraud "
          f"({fraud/n*100:.3f}%)")
    print(f"\nnext:  SENTINEL_DATA={out} SENTINEL_CSV_SCHEMA={name} "
          f"python -m sentinel.train")
    return out


if __name__ == "__main__":
    fetch(sys.argv[1] if len(sys.argv) > 1 else "ulb")
