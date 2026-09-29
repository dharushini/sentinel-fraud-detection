# Replaying dataset transactions live (not just training on them)

`docs/DATA.md` covers training on datasets offline. This is the companion
piece: streaming **historical dataset transactions** through the *live*
dashboard — the same `/score` path the simulator uses — instead of Sentinel's
own synthetic traffic.

> **Provenance, stated plainly.** None of the replayable datasets here is real
> bank data. The Indian banking dataset and PaySim are generated; the UPI file
> appears to be simulator output (see below). They exercise the live pipeline
> on independent data with its own labels — useful for demos and stress tests —
> but results on them are not evidence about a real bank's customers. Validate
> on the bank's own labelled history before production use.

Dataset files live in `data/`, which is git-ignored: they are **not** part of
the repository or of a cloud deployment. The dashboard checks `/replay/status`
and disables the buttons for datasets that aren't present on the server.

## Indian banking dataset (`india_bank`, recommended)

Four CSVs — transactions, customers (with age), cards, merchants — put in
`data/india_bank/`. 227,821 successful INR transactions across Indian states
and cities, 1.38 % fraud, and every customer's **real age from the data**, so
the age panels show dataset ages rather than Sentinel's per-customer fallback.
It is generated data (machine-made names, rule-derived fraud labels), and
customers are sparse (median 9 transactions over 3.6 years), so behavioural
signals are weaker than on dense card data. Click **"Replay 100 Indian bank
transactions"**, or:
```bash
curl -X POST localhost:8000/replay -H 'content-type: application/json' \
  -d '{"schema_name": "india_bank", "limit": 100}'
```

## UPI-style file (`upi`)

A UPI/Razorpay-style transaction file of **unknown provenance**. Despite earlier docs calling it a genuine export, its contents indicate an agent-based **simulator**: an `agent_type` column (normal / fraud / impatient / dormant / …), every transaction on a weekday between 9:00 and 19:00, one IP per device, and devices making 60–779 payments an hour. Useful as UPI-shaped demo traffic, not as evidence about real customers. Put it at `data/upi.csv`. Because its devices
transact at bot-like speed from their first appearance, Sentinel challenges a
large share of its "genuine" rows — which is the right response to 30+
payments in a device's first hour, and a property of the file, not a
benchmark.

## Second dataset: PaySim (download required)

**"Synthetic Financial Datasets For Fraud Detection"** (PaySim) is a
mobile-money simulator with real customer identities (`nameOrig`) and
transaction types (CASH_IN/CASH_OUT/DEBIT/PAYMENT/TRANSFER), useful as a
second, larger generated source:

1. Get a free Kaggle account: https://www.kaggle.com/account/login
2. Download **"Synthetic Financial Datasets For Fraud Detection"**:
   https://www.kaggle.com/datasets/ealaxi/paysim1
3. Put it at `data/paysim.csv` (create `data/` if needed — it's git-ignored):
   ```bash
   mkdir -p data
   mv ~/Downloads/PS_20174392719_1491204439457_log.csv data/paysim.csv
   ```
4. Click **"Replay 100 PaySim transactions"**, or:
   ```bash
   curl -X POST localhost:8000/replay -H 'content-type: application/json' \
     -d '{"schema_name": "paysim", "limit": 100}'
   ```

PaySim's raw `amount` is an abstract simulation unit (often in the hundreds
of thousands), not a currency figure, so the adapter log-compresses it into
Sentinel's realistic INR range rather than relabelling it as-is — see
`_paysim_amount_to_inr` in `csv_adapter.py`. Relative ordering (a bigger
PaySim amount stays a bigger INR amount) is preserved.

Sparkov (Kaggle "Credit Card Transactions Fraud Detection Dataset") and IEEE-
CIS also work the same way (`schema_name: "sparkov"` / `"ieee"`) if you want
US-context data instead — see `docs/DATA.md` for their column layouts.

Check what's already downloaded and ready with:
```bash
curl localhost:8000/replay/status
```

## Why not the one-command `fetch ulb` dataset for this?

`python -m sentinel.datasets.fetch ulb` downloads the ULB dataset
automatically (no Kaggle account needed) and is great for **training/eval**
(see `docs/REAL_DATA_VALIDATION.md`), but its rows are anonymized PCA
components with no real customer, merchant, or location identity — every row
shares the same placeholder `cust_id`. That's fine for judging the model in
isolation, but it can't drive the live per-customer engine (impossible
travel, new-device, new-payee, entity fraud-rate history, etc. all need a
real identity to compare against). `india_bank`, `upi` and `paysim` rows carry
per-customer identities, so they exercise the full live pipeline properly.

## Going further: real-time feeds instead of a static file

`docs/DATA.md` lists a few free sandbox APIs (Plaid Sandbox, GoCardless,
Lithic/Marqeta, Stripe test mode) that push transaction-like events over a
webhook or poll. Wiring one in is the same shape as `/replay`: translate its
payload into the canonical event dict and call `engine.process(txn)` (or
just `POST` it to `/score`) as each one arrives.
