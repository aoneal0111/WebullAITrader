# Separate options and futures learning outcomes

The offline contract outcome importer reads a dedicated OptionsPaper or
FuturesPaper ledger in one read-only SQLite snapshot. It checks engine markers,
durable ledger/intent ownership, one opening and closing event, matching
quantities/instruments, aware timestamps, fees and agreement with settlements.
Open positions are excluded. Conflicting or incomplete closed evidence refuses
the report before anything is imported. Reads are bounded to 5,000 intents,
5,000 settlements and 10,000 journal events with byte and query-time limits.

Options PnL is independently recomputed from recorded simulated fill prices,
the standard multiplier of 100 and both recorded fees. Futures legacy closing
events do not retain contract multipliers. Their PnL is checked against the
durable settlement, explicitly labelled event/settlement agreement rather than
independent multiplier reconciliation. A multiplier is never inferred from a
symbol. Fill prices already include simulator slippage; no extra slippage is
silently deducted.

These are quote-proxy simulations, not broker executions. Input provenance may
be synthetic or observed and is not verifiable from the ledger alone. Policy
version and strategy family remain UNKNOWN because the closed records do not
retain reliable entry-policy attribution. Intent fingerprints are not used as
strategy versions.

LearningJournal stores these outcomes in a separate table, with engine,
ledger and intent in the identity. Exact repeat imports are idempotent; changes
to a closed outcome refuse and roll back the entire batch. Equity outcome
payloads and their identities remain unchanged. Summary groups keep separate
ledgers and evidence/reconciliation scopes separate.

New Grok learning reviews receive a separate quote_simulation_feedback field
beside equity observational feedback. The snapshot is frozen with the review.
These outcomes never create paired comparisons, nominate eligible challengers,
activate policies, or prove strategy profitability. Existing cached reviews are
not rewritten or repeated. The CLI performs no model calls, even when a key is
present. Desktop automatic ingestion remains future work. Crypto has a separate
linked-dispatch importer documented in crypto-learning-outcomes.md.

Run from the repository root, supplying the actual dedicated simulator ledger:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.contract_learning_outcomes `
    --engine OPTIONS --ledger "<your-options-ledger.sqlite3>" `
    --journal data/research/learning/learning.sqlite3
```

Use --engine FUTURES with the dedicated futures ledger. Do not pass the equity
paper-execution database, provider bar capture, or a guessed path. The learning
journal must be a separate file. A missing source is not created. The importer
does not start Atlas or enable entries.
