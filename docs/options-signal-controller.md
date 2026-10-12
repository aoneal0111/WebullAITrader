# Offline options signal controller

This stage connects supplied SPY/QQQ underlying signals to the dedicated
`OptionsPaper` ledger. It makes no network calls and is not wired into Atlas's
running GUI, Grok, the shared allocation controller or a broker. Options data
reads currently return HTTP 403; this does not bypass that blocker or enable
trading. The ledger remains off by default, including after reopening.

## Comparative baseline

`options_signals.propose` reuses the existing 20-bar range breakout and 8/21 EMA
agreement baseline. It requires 22–256 completed, ordered, contiguous one-minute
underlying bars, latest end within one minute, and a fresh underlying quote
whose executable bid (up) or ask (down) still crosses the prior range. Quotes
must be at most two seconds old and not precede the signal bar's end.

An up signal chooses supplied SPY/QQQ CALL contracts; a down signal chooses PUT
contracts. Both buy long premium; neither shorts options. At most 32 distinct
contracts are accepted per call. The caller owns contract provenance, expiry,
strike selection and market-session/halt/tradability authorization. This is not
an automatic chain scanner or verified profitable strategy.

Fresh actual-contract premium quotes determine entry, stop and target. Only
standard 100-multiplier contracts and tick-aligned values qualify. Targets are
explicit premium distances of $0.05 or $0.10, with a hold of at most 60 seconds.
Fees are caller-supplied (default $0.65 per contract per side); they are not a
claim about the user's broker fees. Default slippage is $0.01 per side and stop
distance $0.02. Defaults are comparison settings, not recommended live settings.

Sizing includes entry and exit slippage and fees in stop risk, both fees in
capital, and a cap of five contracts. Expected reward after those costs must
cover modeled stop risk. A $0.05 target may produce no plan under the default
costs; the controller does not loosen costs or increase size to force entry.
Spread ranks alternatives rather than serving as a separate hard percentage
filter. The ledger retains final quote, cash, aggregate risk, per-trade risk,
quantity and one-position-per-underlying checks.

## Parent authority and durable identity

`OptionsPaperController.step` protects existing positions first. Missing
underlying history or disabled new entries does not disable premium exits.
Missing/stale premium quotes remain unresolved; no imaginary exit is generated.
New entries require an exact `True` from a trusted deterministic callback and
explicit ledger activation. Only the first ranked alternative is submitted;
a refusal does not cascade into another strike.

Successful intent identity is tied to policy, underlying, completed signal bar
and direction. It stays consumed after exit and restart, even if the caller
later supplies a different strike for that same signal. Unfilled refusals do
not consume an identity. Existing ledger tables are reused; this adds no schema
migration. Cash and risk remain isolated from the shared equity/futures pool.

## Windows mechanics check

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-options-signals-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_options_signals.py `
    tests/asset_modules/test_options_paper.py `
    tests/asset_modules/test_contract_logic.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Options signal tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.options_signal_check
if ($LASTEXITCODE -ne 0) { throw "Options signal check failed." }
```

The smoke check uses fabricated bars/contracts/quotes and a temporary ledger.
It checks call and put entries and target exits with entries disabled. Its P&L
is synthetic mechanics evidence only; no strategy performance or real fill
claim is made. The temporary ledger is removed when the check finishes.
