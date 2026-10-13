# Completed-history collection phase

After the history-before-quotes repair, the operator's BTC/USD and ETH/USD
capture had contiguous bars. The remaining refused windows had latest bar ages
of approximately 61.6–63.7 seconds, just beyond the existing one-minute guard.
The poller waited 60 seconds after each completed request cycle. Approximately
six seconds of request work made collection drift through the minute boundary.
These observations suggest boundary timing; they do not establish an exact
provider publication delay.

For the default 60-second polling cadence with completed-history intelligence
enabled, the first refresh remains immediate. The first subsequent refresh is
aligned to ten seconds after a minute boundary, no sooner than 60 seconds after
the preceding refresh started. Following cycles compensate for time spent in
requests and use the same phase, while preserving that minimum start spacing.
Wakeup jitter can move the phase slightly later. Slow cycles skip elapsed slots;
there are no catch-up requests or immediate retries. Waiting uses the existing
stop event, so shutdown interrupts a scheduled wait.

Ten seconds is a collection scheduling offset, not a provider availability
guarantee. A provider delay or long cycle can still produce stale history. The
two-second quote and one-minute bar freshness guards, history request budgets,
M1/M5 request cadences, normalization and original availability times are
unchanged. Custom polling cadences and quote-only discovery retain their previous
wait-after-completion behavior. Direct `refresh_once` calls remain unscheduled.

The steady default cycle now has a 60-second start interval rather than 60 seconds
plus request duration. This removes unintended drift and may slightly increase
requests per hour relative to the former implementation, but never beyond the
configured per-minute budgets. No new provider operation or execution authority
is introduced. Existing capture records remain unchanged.

## Verification

The focused regression suite covers forty repeated six-second cycles, initial
alignment, minimum request spacing, wakeup jitter, slow acquisition, clock
rewind, custom cadence, disabled intelligence and interruptible shutdown. The
history ordering suite separately verifies that stale history is still refused.

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-minute-phase-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/crypto_research/test_poll_minute_phase.py `
    tests/crypto_research/test_history_before_quotes.py `
    tests/crypto_research/test_completed_bar_runtime.py `
    tests/crypto_research/test_runtime.py `
    tests/crypto_research/test_bar_capture.py `
    tests/asset_modules/test_crypto_signal_replay.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Minute phase tests failed." }
```

These are offline mechanics tests, not trading performance. A later normal PAPER
launch with discovery, intelligence and capture enabled is needed to check new
provider observations. Keep AI proposals disabled during that check, save the
quote log byte length before launch, and replay from that new offset. Earlier
rejected records cannot validate the changed scheduling.
