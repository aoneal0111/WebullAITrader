# Offline futures signal handoff

`futures_signals` provides one baseline policy, `FUTURES_RANGE_EMA_PAPER_V1`,
and a dedicated `FuturesPaperController` that hands proposals to `FuturesPaper`.
It has no network, broker, GUI activation, Grok calls, or live execution path.
It is not the shared equity admission controller: that controller still requires
full notional capital and its router accepts equity orders only.

## Signal and plan

- Supply the actual contract identity, expiry, multiplier, tick, margin and fees.
  The module neither discovers contracts nor infers broker margin.
- Supply 22–256 strictly ordered, contiguous, completed one-minute contract bars.
  The latest bar must be no more than one minute old; unfinished, mismatched,
  off-tick, future, or gapped data cannot authorize a signal.
- Reuse the existing 20-bar prior-range breakout and 8/21 EMA agreement.
  The latest completed close must break the prior range.
- A fresh contract quote must also confirm the prior range plus one tick:
  bid for long, ask for short. Its source cannot precede the signal bar end.
- The structural stop uses the prior five bars' low/high plus one tick beyond
  the structure. Entry extension is bounded to 0.5 of trigger-to-stop risk by
  default; this is a declared baseline parameter, not a tested optimum.
- Target defaults to three entry-to-stop price-risk units, rounded to ticks.
  Sizing includes entry and exit slippage and both fees, with integer lots,
  at most five contracts, a $25 risk budget and $2,500 margin/fee allocation.
  Hold defaults to 15 minutes and is bounded to one hour and contract expiry.

The ledger independently verifies prices, net reward, cash, margin, risk and
ownership. A proposal or caller's authorization does not bypass ledger limits.
Signal and ledger slippage must agree; the signal cap cannot exceed the ledger
contract cap. This policy supplies no volume, catalyst, session-calendar, roll,
or continuous-contract adjustment model. It must not mix different expiries or
stitched contract bars.

## Offline controller

Each step protects existing positions before assessing an entry. Protection
continues when entries are off, authorization refuses, or the new signal has
insufficient history. The ledger starts with entries off on every restart.

Construction requires a trusted deterministic `authorize(proposal, now)`
callback. Only the exact boolean `True` approves. A future real-data composition
must implement session/tradability/halt and account permissions there; a
synthetic always-true callback is not production authority. AI cannot supply
prices or bypass these checks. Existing ledger quote and risk checks still run.

A successful entry owns a durable identity keyed by policy, contract/expiry,
direction and completed signal-bar end. Repeated ticks and restarting cannot
rebuy that same successful signal even after it closes. Rejected candidates do
not claim the identity and may be reconsidered with current data. Changed
settings do not unlock a previously used bar. `intent_state` checks the durable
intent table, including closed intents beyond the latest 100 journal events.

This is an isolated simulator with its own declared budget. Shared allocation
with equity/options/crypto, worker transport, actual feed adapters, contract
spec verification, exchange sessions, and GUI wiring remain separate work.

## Windows checks

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-futures-signals-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_futures_signals.py `
    tests/asset_modules/test_futures_paper.py `
    tests/asset_modules/test_contract_logic.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Futures signal tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.futures_signal_check
if ($LASTEXITCODE -ne 0) { throw "Signal handoff check failed." }
```

The fabricated breakout smoke check opens only after explicit local activation,
then exits at its fabricated target with entries disabled. This proves mechanics,
not strategy expectancy, executable fills, or profitability. No policy is
promoted and no Atlas process is launched.
