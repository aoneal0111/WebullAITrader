# Bounded crypto reads and observer input

This follow-up repairs research data quality and refresh status. It changes no
strategy rules, execution quote ages, polling interval, risk limits, protective
exits, live settings or Grok entry activation.

## Research freshness and coverage

`CryptoResearchRuntime.admit` now rejects negative quote ages and increments
`future_quotes` separately from stale quotes. A refresh counts as complete only
when every requested pair returns a quote with bid/ask, its age is within the
configured research limit, and it was admitted or was an already known duplicate.
Unrequested pairs cannot enter the queue through a provider batch. Missing pairs,
stale/future rows and rejected admissions produce partial/awaiting-data status.
Transport failures retain their existing error classification and backoff.

New cumulative diagnostics: `crypto_snapshot_symbols_missing`,
`crypto_snapshot_symbols_fresh`, `crypto_future_quotes`. "Fresh" here means the
research age policy (default120seconds), not executable freshness. Last-price
observations without bid/ask can still be recorded as research, but cannot make
the batch complete. Duplicate fresh observations need not be persisted twice to
count as covered. As before, `ACTIVE` is research status, never order permission.

## Workstation probe

The `check-weekend-observer.ps1 -CryptoRead` option first performs the existing
local PAPER/live/Grok flag checks and offline tests. It then runs
`app.crypto_research.readiness` in the disposable source checkout using existing
local scoped market-data configuration. It never merges into the installed
repository, launches Atlas, constructs an order controller, changes `.env`,
requests model inference, purchases services or regenerates credentials.

The probe requests only the configured BTC/USD and ETH/USD instrument identities,
then at most three snapshot calls. Provider retries are off; discovery is bounded
to one page; SDK timeout is5seconds. The provider's1second minimum interval is
retained. Existing Webull authentication is used locally. No credential values,
provider bodies, raw prices or private performance enter the result.

The result separately checks full two-pair coverage, duplicates, bid/ask presence,
future timestamps, age<=2seconds for deterministic entry freshness, and the
default120second research-age comparison. The latter is descriptive and does not
replace the actual runtime's configured research age. An unexpected pair also
fails the sample. A provider failure ends the probe with a sanitized category.

`BOUNDED_SAMPLES_FRESH` requires all three samples to pass. It proves only those
samples, not continuous feed reliability, historical continuity, active runtime
settings, strategy edge or permission to activate crypto proposals. The normal
60second snapshot cadence is unchanged. Failure preserves a small Desktop JSON
and leaves the baseline unchanged. No subscription is purchased automatically.

Direct equivalent, after obtaining the new source:

```powershell
& $Python -m app.crypto_research.readiness --repo $Repo --output $NewOutput --online-read
```

`--online-read` is explicit because this command makes authenticated market-data
reads. Omitting it performs no requests. Keep live trading, Grok order review
and crypto proposals disabled. Do not paste or regenerate working credentials.

## Observer file connection

`app.asset_modules.grok_observer_input.load_snapshots(path, baseline_sha256)`
reads at most1MB and100JSONL records, with a4KB line limit. Each row must contain
exactly the `Snapshot` dataclass fields:

```json
{"engine":"WARRIOR_MOMENTUM_V1","event_id":"example1","symbol":"AAPL","bid":"100","ask":"100.01","bid_at":"2026-10-24T14:00:00+00:00","ask_at":"2026-10-24T14:00:00+00:00","captured_at":"2026-10-24T14:00:00+00:00","baseline_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source":"RECORDED_FIXTURE"}
```

The example is synthetic. The other allowed owner is `QUICK_SCALPER`. Allowed
sources are `RECORDED_FIXTURE`, `WEBULL_OPENAPI`, `ALPACA_SIP`; a label is not an
independent authenticity proof. Do not label real records as fixtures or alter
source timestamps to make old data appear fresh. Frozen baseline identity must
match. Duplicate JSON keys, duplicate owner/event identities, out-of-order owner
timestamps, stale/future-at-capture quotes, other engines and extra fields
(including credentials/order instructions) are rejected before any advice call.

`observe_file(path, trial, baseline_sha256)` then sends typed snapshots only to
the explicitly injected disconnected observer. Its clock must still pass the
observer's independent freshness checks. Old saved quotes cannot authorize a
current model review merely because they were fresh when recorded. Offline tests
use an explicitly controlled replay clock and fake transport. There is no online
CLI, environment activation, desktop hook, order dispatcher, file writer or
automatic history upload in this adapter.

Read-only fixture tests verify unchanged input hashes and that a malformed later
row prevents all provider calls, including the earlier valid row. Actual live
export/tailing, strategy context, private local logging and usage accounting are
still required before a useful online observer trial.
