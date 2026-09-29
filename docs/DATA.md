# Training Sentinel on real data

Sentinel ships with a seeded **synthetic** generator so it runs with zero setup.
To train on another labelled transaction dataset instead, point it at a CSV (check each dataset's provenance — several popular "fraud" datasets, including two below, are themselves generated):

```bash
SENTINEL_DATA=/path/to/file.csv SENTINEL_CSV_SCHEMA=<schema> python -m sentinel.train
python -m sentinel.eval        # then evaluate
```

All of the datasets below are **free** (Kaggle account required to download, no
payment). The adapter (`sentinel/datasets/csv_adapter.py`) uses only the Python
standard library, so it streams large files without pandas.

| `SENTINEL_CSV_SCHEMA` | Dataset | Fit | Notes |
|---|---|---|---|
| `sparkov` | [Credit Card Transactions Fraud Detection](https://www.kaggle.com/datasets/kartik2112/fraud-detection) (`fraudTrain.csv`) | **best** | timestamp, card number, merchant, category, amount, lat/long — maps cleanly to every behavioural + entity feature |
| `ieee` | [IEEE‑CIS Fraud Detection](https://www.kaggle.com/competitions/ieee-fraud-detection) (`train_transaction.csv`) | good | rich but obfuscated; no geo. `(card1, addr1)` is the customer proxy, `DeviceInfo` the device, `P_emaildomain` a payee‑like entity |
| `ulb` | [Credit Card Fraud Detection](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud) (`creditcard.csv`) | raw mode | features are PCA components `V1..V28` — Sentinel skips feature engineering and trains the GBM directly on them; behavioural/entity/graph features and most rules are inactive |
| `upi` | UPI/Razorpay-style transaction file (timestamp, amount, currency=INR, upi_app, bank, device_fingerprint, status, is_suspicious) | demo only | a UPI/Razorpay-style transaction file of **unknown provenance**. Despite earlier docs calling it a genuine export, its contents indicate an agent-based **simulator**: an `agent_type` column (normal / fraud / impatient / dormant / …), every transaction on a weekday between 9:00 and 19:00, one IP per device, and devices making 60–779 payments an hour. Useful as UPI-shaped demo traffic, not as evidence about real customers. `device_fingerprint` is the customer proxy, `upi_app`+`bank` form the merchant id, country `IN`; only `status == success` rows are loaded. Not committed to git (unknown licence) — place it at `data/upi.csv` yourself |
| `paysim` | [Synthetic Financial Datasets For Fraud Detection](https://www.kaggle.com/datasets/ealaxi/paysim1) (PaySim, `.csv`) | good | mobile-money simulator, ~6.3M rows; `nameOrig` is the customer, `type` (CASH_IN/CASH_OUT/DEBIT/PAYMENT/TRANSFER) maps to channel/mcc, `step` (1 simulated hour) is the timestamp; **amounts are log-compressed into Sentinel's realistic INR range** (`_paysim_amount_to_inr` in `csv_adapter.py`) since PaySim's raw `amount` is an abstract simulation unit, not a currency figure — relative ordering (bigger PaySim amount → bigger INR amount) is preserved |
| `india_bank` | Indian retail-banking dataset in four CSVs — `Transaction_Data_250k.csv` + `Cusmtomer_data.csv` (customers **with age**) + `Cards_Data.csv` + `merchant_table.csv` | good (Indian context) | 250k INR transactions across Indian states/cities, RuPay/Visa cards, UPI/net banking, 25k customers aged 18–70. **Generated data** (machine-made names; each fraud reason is 100 % fraud, i.e. rule-labelled), but the most India-specific source available and the only one with real per-customer ages, which flow straight into Sentinel's age categorisation. Only successful transactions are loaded (a decline is the existing system's verdict — 59.6 % of declines are fraud — so using it would leak the label). Point `SENTINEL_DATA` / the replay path at the transactions file; the customer file is read from the same folder. Place the four files in `data/india_bank/` |

Example:

```bash
SENTINEL_DATA=~/data/fraudTrain.csv SENTINEL_CSV_SCHEMA=sparkov python -m sentinel.train
```

The time‑ordered train/valid/test split, isotonic calibration, cost‑sensitive
threshold, drift reference and grouped counterfactual explainer all work identically on real data.

---

## Adding another schema

Add a generator function to `csv_adapter.py` that yields the canonical event
dict and register it in the `fn` map in `load_csv_events`:

```python
{
  "type": "txn", "ts": <datetime>, "cust_id": str, "amount": float,
  "mcc": str, "channel": "pos|online|atm|transfer",
  "merchant_id": str, "beneficiary": str, "card_bin": str,
  "country": str, "city": str, "lat": float, "lon": float,
  "device_id": str, "label": 0 | 1,
}
```

Login events (optional, improves the account‑takeover signal):

```python
{"type": "login", "ts": <datetime>, "cust_id": str, "device_id": str, "success": 0 | 1}
```

---

## Live data feeds (all have a free tier / sandbox — none billed)

These are *not required*; they replace the simulator with a real stream.

| Source | Free tier | Shape |
|---|---|---|
| **Plaid Sandbox** | free, instant, no card | `/transactions/sync` — realistic synthetic bank transactions; **Plaid Signal** returns an ACH risk score |
| **GoCardless Bank Account Data** (ex‑Nordigen) | free tier | **real** EU bank transactions with user consent |
| **Lithic** / **Marqeta** sandbox | free sandbox | real‑time **card authorization webhooks** — the network asks you to approve/decline in ~2 s; this is exactly where `POST /score → BLOCK` sits |
| **Stripe** test mode + Radar | free test mode | simulate charges, observe fraud scoring |

To wire one in: translate its webhook/poll payload to the event dict above and
call `engine.process(txn)` (or `POST /score`).
