# Linked crypto simulation learning outcomes

The offline importer reads the dedicated CryptoPaper ledger in a consistent,
read-only SQLite snapshot. It joins durable crypto_intents and crypto_dispatches
to journal events by exact identities. One BUY and all partial/full SELLs must
agree on instrument, quantities, timestamps and simulator model. Fully sold
quantity and final timestamp must agree with the closed intent. A live position
link cannot coexist with a closed intent. Open/partially sold entries are
excluded after checking their remaining inventory. Legacy records without
durable entry links are counted and excluded; no symbol/time guess joins them.

Net PnL must agree with recorded linked exit PnLs. Native crypto uses 28-digit
sequential Decimal accumulation; the reader accepts that exact rule or exact
high-precision accumulation. It does not use a monetary tolerance. Source
amounts support the native fractional precision and remain explicitly bounded.
No fees or slippage are inferred from today's simulator configuration. Legacy
records do not retain separately auditable fee amounts: recorded_fees is UNKNOWN
and reconciliation is linked-dispatch PnL agreement, not independent fee or
cash-flow reconstruction. It is not complete account reconciliation.

These are snapshot simulations, not broker executions or validated strategy
performance. The ledger cannot prove whether input quotes were observed or
synthetic. Policy versions/families remain UNKNOWN; dispatch fingerprints are
not strategy versions. Outcomes share the separate proxy outcome journal table
with options/futures, but feedback stays grouped by engine, ledger and evidence
scope. Equity outcomes are unchanged. Repeat imports are idempotent; conflicting
closed payloads refuse the entire batch. New learning reviews freeze separate
quote_simulation_feedback. No comparisons or eligibility are manufactured from
outcomes, and old reviews are not repeated or rewritten.

Limits: 5,000 intents/links/positions, 20,000 dispatches/journal events, bounded
query bytes and a ten-second source query deadline. A missing ledger is not
created. Too-large/inconsistent sources refuse instead of importing a partial
sample. Source schema migration is never performed. Automatic desktop ingestion
remains separate work; this CLI makes no network/model calls or orders and does
not start Atlas.

Use the actual configured CryptoPaper ledger, not crypto provider capture files:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_learning_outcomes `
    --ledger data/sandbox/crypto-paper.sqlite3 `
    --journal data/research/learning/learning.sqlite3
```

The example path applies when the desktop equity execution ledger is
data/sandbox/paper-execution.sqlite3. Desktop puts the crypto ledger beside it;
custom execution paths require the corresponding sibling crypto-paper.sqlite3.
Check existence before importing. Keep the learning journal separate.
