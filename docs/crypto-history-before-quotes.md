# Capture completed crypto history before fresh quotes

Actual captured BTC/USD evidence showed fresh quotes (approximately 0.3–0.9
seconds old) and contiguous bars, but the latest eligible bar at each quote cutoff
was 75–104 seconds old. Crypto replay correctly refused these frames under its
one-minute completed-history freshness guard.

The runtime previously requested and admitted snapshots before refreshing and
capturing provider bars. Newly returned history was therefore unavailable at an
already recorded quote cutoff. The replay's first-availability filter correctly
selected older history; changing that filter would introduce future information.

`CryptoResearchRuntime.refresh_once` now performs its existing bounded history
refresh/capture after pair discovery and before requesting snapshots. A subsequent
fresh snapshot can be recorded against history already available at its decision
cutoff. History request count, budget, cadence and normalization are unchanged.
No extra snapshots, historical requests, immediate retries or worker activations
are added. History failure remains separate from snapshot admission.

The two-second quote guard and one-minute history guard remain unchanged. A slow
provider history cycle can still leave history too old; such a frame is refused.
This sequencing repair does not guarantee readiness, trading signals or profit.
Existing saved quote cutoffs and bar availability times are never rewritten.
Old observations remain ineligible when they were ineligible originally; only
new collection uses the corrected sequence.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-history-before-quotes-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/crypto_research/test_history_before_quotes.py `
    tests/crypto_research/test_completed_bar_runtime.py `
    tests/crypto_research/test_runtime.py `
    tests/crypto_research/test_bar_capture.py `
    tests/asset_modules/test_crypto_signal_replay.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "History ordering tests failed." }
```

Regression coverage includes a previously stale saved window, first availability
at the new cutoff, unchanged old replay results, fresh source timestamps, unchanged
request counts, history-provider failures, disabled history and deliberately slow
history acquisition. These tests use injected provider responses, not real market
requests or strategy performance.

The running Atlas process must restart through normal operator shutdown to use
new code. On the next normal PAPER launch, retain the existing discovery,
intelligence and capture settings. Leave AI proposals disabled. Allow new capture
records, then assess those later quote records with the saved replay offset; the
old rejected observations are not evidence that the new sequence failed.
