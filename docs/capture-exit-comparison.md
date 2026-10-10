# Captured Warrior exit comparison

`capture_exit_comparison` compares two frozen full-size exit proxies after the
last actual entry fill: structural stop plus maximum hold, and the same stop/time
policy with an armed peak-profit retention exit. It changes no trading runtime,
GUI, controller, admission policy or historical artifact.

This is not an exact replay of Warrior's partial targets, harvests or trailing
stops. Actual SELL fills are used only for reconciliation and the capture end;
they never reduce hypothetical position size. Actual closed PnL is a separate
reference and is excluded from paired proxy totals. The baseline is an experiment
baseline, not the live Warrior champion.

Default activation is 0.4 times initial price risk; retention is 50% of the
highest previously observed net profit. These are hypotheses, not recommended
production thresholds. Stop takes priority, then maximum hold, then retention.
Prices are sampled bids with no queue, size, liquidity, market impact, order
latency or portfolio model. A bid mark does not establish a full-size fill.

Recorded BUY commissions are charged once. Declared exit fee per share and extra
cost per share per side are charged separately. Defaults add no estimated cost;
that does not establish cost-free execution. Actual closed PnL includes its
recorded commissions, not the declared hypothetical exit cost.

A gap exceeding five seconds, a captured missing-interval flag, stale/future
quote, crossed or missing sides, nonincreasing observed timestamp, or ambiguous
same-time fill makes each still-open policy unresolved. Invalid samples are not
skipped to find a later favorable price. The entry-completion-to-first-quote gap
is checked too. A policy closed before a later gap stays closed, but paired totals
require both policies closed on that same lifecycle. Capture ending without a
policy exit is unresolved. Sparse or timing-invalid captures may yield no pairs.

Only fully reconciled, closed Warrior lifecycles enter the comparison. Other
lifecycles are listed with their reconciliation failure. Exact duplicate
campaign/lifecycle inputs are rejected. Settings and input content are hashed;
trading dates use New York time. CLI inputs are bounded to twenty files and
20 MB combined; each capture retains the existing 5,000 record/500 order limits.
No automatic policy promotion occurs.

## Windows validation

```powershell
Set-Location "$env:USERPROFILE\WebullAITrader"
$TestRoot = Join-Path $env:TEMP ("atlas-capture-exits-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/trade_intelligence/test_capture_exit_comparison.py `
    tests/trade_intelligence/test_capture_profit_audit.py `
    tests/asset_modules/test_strategy_comparison.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Capture exit tests failed." }

& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.capture_exit_comparison `
    data/research/warrior_capture_audit_v1/naut_cci_capture.json
if ($LASTEXITCODE -ne 0) { throw "Capture comparison failed." }
```

For additional captures, pass explicit `batch_*_capture.json` files produced by
`capture_profit_export`. Do not recursively combine repeated exports of the same
trades. Large excluded counts are a data-coverage finding, not a reason to loosen
coverage limits or deploy the candidate exit policy.
