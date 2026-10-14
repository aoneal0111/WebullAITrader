# Equity outcomes into learning memory

The equity importer reads a consistent snapshot of the active campaign in the
existing PAPER execution ledger. Sources open with SQLite `mode=ro` and
`query_only`; missing sources are never created. Reads have a ten-second query
deadline, 5,000-order and 32 MB bounds. It imports complete lifecycle buy/sell
fills, with net cash P&L less recorded commissions. It does not sum runtime event
P&L or reconstruct fills from quotes.

Orders must be terminal, recorded filled quantities must match fills, inventory
must balance without premature closure/re-entry, and fill IDs must be unique.
Incomplete lifecycles are excluded with counts. Invalid numeric values, duplicate
fills or inconsistent quantities refuse the entire source read. Unknown owners
and strategy versions stay unknown; explicit ownership conflicts are excluded.
The controller's per-order `policy_version` hash is not a frozen strategy version.
Only explicit `strategy_policy_version` metadata supplies that field.

Learning outcomes are keyed by campaign/lifecycle/instrument. Exact reimports
do not increase sample counts; changed closed outcomes cause an atomic import
failure requiring source reconciliation. The dedicated learning journal now
also contains `learning_outcomes_v1`, preserving existing experiment reviews.

Outcomes provide observational engine/policy totals, wins, losses and breakeven
counts. These are not paired policy comparisons, portfolio drawdown, independent
holdout samples or complete account reconciliation. Historical records may lack
fees or policy identity. No additional cost or regime is invented. Source
provenance stays explicitly unverified: a ledger file alone cannot prove its
quotes came from real observations rather than synthetic inputs.

The Grok reviewer receives a frozen copy of outcome feedback beside the original
paired comparison, and retains that copy with its review. Observational outcomes
never create challenger eligibility or trigger strategy activation. An optional
`outcome_source` callback lets the explicit learning loop ingest before review;
source failure prevents that tick's review. No desktop subscription is added.

## Offline import

With Atlas closed, use a separate learning journal:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.learning_outcomes `
    --ledger data/sandbox/paper-execution.sqlite3 `
    --journal data/research/learning/learning.sqlite3
```

Run again to verify `inserted: 0` and unchanged counts. No credentials, network
requests or orders are involved. Unknown policy groups cannot establish which
strategy version produced their results. An audit may differ from the earlier
cash summary because this importer additionally rejects ambiguous inventory
sequences, ownership conflicts and fill inconsistencies.

## Remaining asset adapters

Crypto records linked intents/dispatches and net exit amounts. Options/Futures
record quote-simulator settlements, not broker fills. Older Futures settlements
do not retain enough contract information to independently derive P&L from
prices. They need distinct adapters and provenance checks; their settlements
must not be relabelled as equity fill evidence. This stage handles Warrior and
Scalper only, plus explicitly unknown equity ownership for reconciliation.
Automatic desktop ingestion, new trial scheduling and policy activation remain
separate integration tasks. No Grok API call or runtime policy change is enabled.
