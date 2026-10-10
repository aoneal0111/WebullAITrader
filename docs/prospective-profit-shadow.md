# Prospective Warrior profit shadow

New PAPER Warrior lifecycles record a versioned observation-only peak-profit
candidate in the existing execution-price-path capture. There is no order
submission, stop mutation, exit authority, AI request or automatic promotion.
Scalper does not receive Warrior shadow state.

The observer waits until a BUY order is filled or its remaining entry quantity is
cancelled/expired. It uses actual BUY costs, actual SELL cash flows and remaining
shares marked at the observed bid. Entry structural stop comes from the durable
PAPER order via an optional projected field, not from a newer scanner setup.
The field is backwards-compatible with older durable event payloads.

Activation is a hypothesis of 0.4 times initial price risk. A signal records a
fall below 50% of the highest previously observed gross total profit. ARM and
SIGNAL each emit once. These are gross marks: commissions and hypothetical exit
costs are not included because runtime fill projections do not carry them. The
records are not executable fills, exact first crossings or verified net profits.

`PROFIT_SHADOW_UNAVAILABLE` records the first gap, future/stale/invalid quote,
nonincreasing changed provider timestamp, missing/changed structural stop,
invalid risk, or additional BUY after observed management has begun. Such paths
never resume as complete evidence. Restart recovery remains explicitly labelled
unavailable before restart and does not reconstruct a shadow peak.

Changed bid/ask values with advancing provider timestamps now bypass the
one-second unchanged-price heartbeat throttle. Exact repeated sides retain the
heartbeat throttle; nonincreasing timestamps remain suppressed from QUOTE
sampling and invalidate the shadow when changed or timing-invalid. Invalid quote
ages cannot hide behind unchanged-price throttling. Existing active-path cap
128, quote sample cap 20,000 and bounded writer handoff still apply. Shadow state
retains at most 512 fill identities and at most three transition records per
path. Closed paths release state. Busy quotes can reach capture limits sooner;
CAPTURE_LIMIT_REACHED remains explicit. No subscriptions, network polling or
synthesized missing quotes are added.

This can capture previously discarded subsecond price changes. It cannot repair
upstream feed gaps, clock skew, writer drops or recordings from earlier runs.
Captured shadow timing/quote coverage failures also invalidate still-open paths
in the offline captured-exit comparison, including changed prices whose provider
timestamp did not advance. Non-coverage shadow failures such as missing stop or
entry mutations do not silently become quote-coverage failures.

## Windows checks

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-profit-shadow-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/warrior_momentum/test_profit_retention_shadow.py `
    tests/warrior_momentum/test_forward_capture.py `
    tests/paper_gateway/test_gateway.py `
    tests/paper_gateway/test_durable_store.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Profit shadow tests failed." }
```

Restart Atlas before prospective PAPER observation. The Saturday market closure
will not provide normal equity PAPER trade evidence. After the next PAPER run,
existing capture exports contain PROFIT_SHADOW_ARMED, PROFIT_SHADOW_SIGNAL and
PROFIT_SHADOW_UNAVAILABLE alongside fills and quotes. Review signal timestamps,
gross marks, unavailable reasons and subsequent actual fills separately.
