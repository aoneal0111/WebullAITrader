# Strategy replay verification

The daily IBS and Donchian screen has a separate test oracle in
`tests/strategy_research/daily_reference.py`. It calculates signals, initial
stops, fill costs, exits and returns without importing the screen's execution,
signal or metric functions. It follows each trade to completion rather than
using the screen's pending-order and position state machine.

Run the differential checks with the project's Python environment:

```powershell
python -m pytest tests/strategy_research/test_daily_reference.py -q
python -m pytest tests/strategy_research -q
```

Six deterministic price paths, all four frozen daily adaptations, two evaluation
windows and three cost assumptions produce 144 differential cases. Every trade
field must match exactly. The scenarios collectively require signal exits,
structural stops, opening gaps, holding deadlines and end-of-window marks.
A separate isolation check disables production signal and cost methods while
running the oracle.

Both implementations deliberately model the same assumptions. Agreement checks
implementation consistency, not the realism of those assumptions. The oracle
covers daily IBS and Donchian only; it does not validate intraday, crypto,
futures or options fills, or reproduce the existing Warrior/Quick Scalper runtime.

Costs remain explicit assumptions until contemporaneous bid/ask quotes can be
matched to broker fills with side, quantity, fees, decision time and order time.
Paper fill summaries alone cannot establish live execution costs. Historical
candles cannot establish order queues, partial fills, executable stop liquidity
or latency. Stop-first ordering is a convention when OHLC cannot establish the
intrabar path; an opening gap through a planned entry stop cancels the modeled
entry and should be checked against actual order behavior before deployment.

Per-symbol equal-risk summaries are not a portfolio simulation. Production
qualification still requires shared-capital sizing, concurrent exposure and
mark-to-market drawdown, untouched dates, and frozen-rule forward paper evidence.
Excluding end-of-file marks from closed-trade win rates can censor unresolved
positions; those marks remain visible and must be included in a portfolio equity
assessment. No verification test authorizes trading or establishes profitability.

The research evidence gate requires an explicit Boolean
`portfolio_replay_passed`, defaulting to false for older evidence. This is an
externally reconciled attestation, not a portfolio simulator or automatic audit.
It may be set true only after checking shared capital, risk-based share sizing,
concurrent exposures, fees and marked open positions. Passing still permits
manual review only.

## Shared-capital daily screen

`app.strategy_research.portfolio_replay.portfolio_replay` accepts a mapping of
symbol to daily `Bar` tuples, one frozen `Adaptation`, explicit `Costs` and
`PortfolioLimits`, and an optional integer start/end window. It recomputes
signals and positions under shared capital; it does not discard losing trades
from a previously calculated trade list.

Limits specify initial cash, per-entry risk fraction, total reserved initial-risk
fraction, per-symbol notional fraction, gross notional fraction and maximum
positions. All fractions are positive Decimals, leverage is excluded, and all
symbols must have identical supplied calendars. Quantity is rounded down to
whole shares. Entry sizing includes the stop's modeled sell execution and both
fees. Cash and exposure calculations reserve immediate liquidation costs.

Opening gap and prior-close signal exits release cash before same-open entries.
Intrabar stops and holding-time close exits release cash afterwards. Same-open
entry priority is alphabetical, fixed before results are seen. Opening sizing
uses opening marks, not that day's future close or total volume. Prior completed
signal bars must have positive volume. This intentionally avoids the older
single-symbol screen's retrospective entry-day volume check; comparisons of
the two engines must account for zero-volume entry bars.

Open inventory is retained at the evaluation boundary. Both cash and liquidation
marked equity are reported, including exit costs, along with realized/open P&L,
closed-trade win rates, sampled open/close equity drawdown, exposure, rejection
reasons and every accepted trade. Sampled drawdown is not intrabar worst loss.
Different quantities and entry rejections change later equity, sizing and trades.

This simulator does not establish live fill quality, portfolio diversification,
settlement feasibility, or future profitability. Its result explicitly retains
`portfolio_verification_complete=False`; it cannot set the review gate's evidence
flag. Initial limits are caller-supplied scenarios, not account configuration.
