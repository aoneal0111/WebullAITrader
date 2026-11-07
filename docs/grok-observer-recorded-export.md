# Read-only saved market evidence archive

The current forward capture does not provide a complete online observer input.
Its `EXECUTION_PRICE_PATH` quote producer retains an aggregate quote timestamp,
bid/ask, and a lifecycle identity for already owned PAPER paths. It does not
retain independently sourced side timestamps, quote-provider provenance and a
complete contemporaneous Warrior/Quick Scalper assessment in each quote record.
Its completed minute bars retain recorded observation time, not independently
verified durable availability. An old record cannot be retimed into fresh data.

`check-weekend-observer.ps1 -GrokExport` exports only the available saved market
evidence. It opens the installed forward SQLite source with `mode=ro` and
`query_only=ON`, in a bounded read transaction. No writer, broker, model, GUI or
trading controller is constructed. The installed source is never initialized
or migrated. Missing source paths stay missing.

The source is `data/warrior_momentum_v1_forward/forward_capture.sqlite3` below
the supplied repository. The module also supports an explicit `--source` path.
Reads cover at most 20,000 recent integer sequence slots and select at most
1,001 bar/price-path records to detect a 1,000-row result cap. Total result bytes
are bounded to 8 MiB, individual payloads to 16 KiB and SQLite execution to the
existing ten-second read deadline. Missing, malformed, oversized, future and
incompatible records remain visible as rejection/error counts. A limited scan
does not establish full historical coverage; bars and quotes can be sparse.

A unique Desktop output directory contains:

- `summary.json`: sanitized counts, limits, source-code fingerprints and explicit
  online-readiness blockers. Send this small file for review.
- `private-market-evidence.jsonl`, if usable rows exist: local recorded OHLCV and
  aggregate quotes only. Keep this file local. Accounts, fills, quantities, PnL,
  full lifecycle IDs and arbitrary reason text are omitted.

Lifecycle ownership is labeled only when its recorded prefix explicitly matches
Warrior or Quick Scalper. Unknown ownership stays unknown. No Warrior record is
copied to Scalper; no setup state or baseline result is invented. Separate side
timestamps and provider provenance remain explicitly unavailable. Source-code
hashes are traceability evidence, not effective runtime configuration attestation.

`ARCHIVE_ONLY_NOT_ONLINE_READY` is a successful archival result and a negative
online-readiness result. The output intentionally cannot be loaded by the strict
observer snapshot adapter. It makes no data/Grok requests, changes no trading
settings, and grants no execution authority. `ATLAS_GROK_PAPER_ENTRIES_ENABLED`
stays false and the PAPER strategy baseline and protective exits stay fixed.

A fresh online trial still requires a separate one-way live context producer
with actual engine assessments and source side timestamps, private local audit
logging, xAI entitlement validation and explicit spend bounds. The exporter does
not supply that producer or activate a trial. Offline fixture success and this
archive must not be presented as online model behavior or strategy performance.
