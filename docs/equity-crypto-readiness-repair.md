# Equity shutdown and native crypto readiness repair

An authoritative observer can fail after STOP has checked worker health but
before its queue drains. The old unbounded `Queue.join()` then waits forever
on the abandoned tail. STOP now uses an ordered sentinel and a real deadline.
It preserves the original failure, reports abandoned events separately from
attempted events, and rejects subsequent publishing. It does not resume an
unreconciled authoritative lane or suppress a trading invariant.

The performance checkpoint now preserves the innermost authoritative stage,
symbol, event type and bounded exception chain. Later normal cleanup does not
replace this record with the live timing marker. Diagnostic frames omit
locals, source text and absolute paths; credential-like messages are redacted.
This repairs shutdown and failure visibility. An existing recording containing
only `AuthoritativeLaneFailure` does not reveal its original exception, and
this change must not be described as a fix for an unidentified original cause.

## Crypto behavior

The pinned NFI strategy, account sizing, entries, exits and adjustments remain
unchanged. Native analyzed snapshots expose source BUY flags, core indicator
readiness, the source global protection flag and its USD activity check.
Fresh input alone is not an executable signal. Counts represent observations,
not unique opportunities, fills, wins or a forward win rate.

Fresh universe selection ranks positive quote-volume pairs from the pinned
Kraken USD universe and screens primary candles using the existing source
288-interval / maximum-60-empty rule. Absent intervals count as empty; no
prices or volumes are fabricated. Selection takes up to 40 eligible pairs,
requires at least 10 and stays inside the source blacklist/universe. Higher
timeframe preflight checks remain representative; native processing validates
individual pairs before entry. This is not a claim that every selected pair
will remain eligible or that more orders or profit will result.

Existing selections remain sealed. A manual `--refresh-universe-once TOKEN`
requires the native worker lock and no open trades or working orders. It
archives both selection files, rolls them back if refresh fails and records
a completion marker so a later restart does not repeatedly reselect. The
existing SQLite wallet, closed trades and source strategy are retained.

Final capture publication now runs even when native worker cleanup fails.
Status distinguishes a closed capture, stale checkpoint and recent checkpoint;
it explicitly does not claim an operating-system liveness check.

## Operator workflow

Run `tools/repair-equity-crypto-readiness.ps1` with both engines stopped, the
reviewed revision, the prior equity trial and the original NFI paper root.
`-RefreshCryptoUniverse` prepares the explicit one-time refresh for the next
manual crypto start. It performs no market requests during installation.

The script runs focused Windows checks and native container checks before a
fast-forward update. It exports a private stopped-equity review, creates a
backup branch, prepares a fresh V6 account-sized extended-session equity trial
and separate repaired crypto launchers. Existing trials and crypto launchers
are retained. The new crypto launcher uses the original wallet/container name.
Nothing starts automatically; Grok remains embedded in Warrior only.

The equity review keeps matching process checkpoints, including earlier
consumer failures even when the final shutdown was a normal GUI close. Closed
journal files are included only when their combined size is at most 256 MiB;
the report declares whether the full journal was included. No missing
traceback is reconstructed. Keep this archive private.

## Validation

- The deterministic failure-during-drain regression reproduces a STOP hang
  against the prior lane implementation and completes with this change.
- Runtime, shutdown, performance, Grok Warrior, account sizing, Scalper and
  stopped-export regression suites run offline.
- Native NFI tests cover unchanged source/config seals, entry pause and exits,
  source-readiness observations, stale/closed status, universe filtering,
  refresh rollback/retry, wallet retention, idempotence, open-position refusal
  and capture closure when worker cleanup raises.
- Server validation uses Python 3.13 for Atlas and Python 3.14 / Freqtrade
  2026.9 for native NFI. The Windows installer repeats native checks in the
  pinned official Docker image. No GUI, real account or live trading is started
  as part of the server checks.
