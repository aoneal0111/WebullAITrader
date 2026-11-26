# Earlier Momentum entries, independent Scalper, and history diagnostics

Reviewed 2026-10-24. No new equity strategy is released for orders. Existing
scanner discovery stays intact. This change adds evidence collection and binds
future policy approvals to their own engine; it does not claim a profitable bot.

## Separate engines

Momentum (`WARRIOR_MOMENTUM`) and Scalper (`QUICK_SCALPER`) keep separate strategy
identities, lifecycle management and attribution. They share market delivery and
account-level risk controls. The mandatory desktop admission boundary now checks
both the approved policy's engine owner and the order lifecycle's engine prefix.
Matching code/config hashes from another engine cannot authorize a BUY. This also
applies to preserving inherited working BUYs. Protective SELLs and cancels retain
their existing paths. The release registry remains empty for both engines.

This is an additional ownership check, not evidence that the existing runtimes
were one bot. Scalper's entry and exit settings have not been changed to swing
settings. No overnight strategy was installed.

## Entry timing investigation

Source inspection found multiple legacy strategy waits. `detect_hod_breakout`
waits for the latest one-minute close above buffered prior resistance, plus its
consolidation and volume conditions. `detect_micro_pullback` already exists, but
also waits for a completed candle above a buffered pullback high and checks
impulse size, pullback depth/shape and reduced selling. Thus the software does
not universally require a new HOD; the selected path and its surviving funnel
checks matter. Current source inspection cannot identify the cause of a past
missed trade without that trade's contemporaneous capture.

The current empty qualification registry separately blocks new equity BUYs. It
would still do so after deleting a legacy breakout condition. Do not remove it
merely to make the trade count rise.

## Fixed early-entry hypothesis

`PRE_HOD_PULLBACK_10PCT_V1` belongs only to Momentum and remains offline research.

- Watch after the known regular-session high reaches 10% above the previous
  available complete regular-session close. A 10% move alone is not a buy.
- Require a three-bar rising-close impulse ending at the known high, followed by
  one to three non-green bars with successively lower highs.
- At the pause close, arm a one-minute resting buy stop one cent above the last
  pause high, strictly below the already known HOD. Decisions run 09:35-15:30 ET.
- Trigger on the following minute's high touching the previously known level;
  do not wait for that recovery candle to close or for a new HOD.
- Model the entry at the worse of next open and trigger plus adverse costs.
  Skip gaps through the structural stop and assumed fills at/above the old HOD.
- Stop one cent below the pause low, defined before entry. One entry per symbol
  per session; flat at session close. No spread or RVOL acceptance gate is added.

Two fixed management variants use the same entry rules and initial stops:
`FULL_2R` takes the entire position at 2R; `HALF_1R_HALF_2R` takes half at 1R,
then moves only the remainder's stop to raw entry starting with the next candle,
and targets 2R for the remainder. A known stop wins a same-candle ambiguity.
The raised stop is never applied retroactively inside the partial-fill candle.
Breakeven price still loses fees/slippage; a partial is not a guaranteed winning
trade. Fractional halves and linear per-share costs are research assumptions;
whole shares, minimum commissions and partial-fill liquidity need validation.

This compares 3 already-seen date windows, 2 management variants and 4 fixed cost
scenarios, retaining every symbol cell and zero-trade result. It exports only
small summaries. The four current stocks and two ETFs may have too few 10% days:
this history is not a point-in-time small-cap movers universe. Missing sessions
can also make a previous available close differ from the previous market close.
No production adapter, latency measurement, shared-capital simulation, independent
reference or new holdout is supplied by this experiment.

## Second-vendor history diagnostic

`HistoryCheck` and the combined `EntryTiming` runner verify the original frozen
plan/source identities and local file hashes, reconstruct regular-session daily
OHLCV from the original minute bars, and make at most six unauthenticated public
Yahoo daily-chart requests. Each is bounded to 2 MB with a 20-second timeout;
there is no retry, credential lookup, login fallback or subscription purchase.
A redirect is refused. Raw second-source responses and their hashes stay beside
the small output in a newly created `.source` folder. Original history is read
without modification.

Compare raw quote OHLC, deliberately ignoring adjusted-close. Daily price
thresholds are the larger of two cents or 10 basis points of the local value;
these are diagnostic tolerances, not trading gates. Returned split events make
comparison inconclusive until adjustment conventions are reconciled. Missing
prices and provider failures remain explicit. Volume disagreement is descriptive
because sale-condition and auction accounting can differ between vendors.
Yahoo's public endpoint can throttle or change; its failure does not invalidate
Alpaca data. A difference does not establish which feed is wrong. Agreement does
not verify minute ordering, opening-five volume, bid/ask quotes or executable
fills. Vendors may share upstream exchange data. Never call this a certification.

Run through `tools/run-local-equity-audit.ps1 -Mode EntryTiming -Revision <reviewed SHA>`.
It discovers existing local history, uses a disposable pinned source worktree,
runs the new focused tests, then returns one JSON named by `UPLOAD_SUMMARY=`.
No API keys, raw-history upload, installed checkout merge or Atlas launch is
needed. Native Windows execution and real-data results remain pending.

## External evidence reviewed

| Source | Evidence available | Atlas implication |
|---|---|---|
| [Warrior micro pullbacks](https://www.warriortrading.com/pull-back-trading-strategy/) | Practitioner explains short pauses, pullback-local triggers and partial exits. | Motivation for an independently written, fixed early-entry hypothesis; our precise rules differ. |
| [Warrior earnings](https://www.warriortrading.com/ross-camerons-verified-day-trading-earnings/) | Public page links multi-year broker statements and describes an accountant report. Some headline figures are gross. | Human trader evidence is not an audited automated strategy or transferable win rate. No Atlas result follows from it. |
| [Connors RSI research](https://tradingmarkets.com/recent/why_rsi_may_be_one_of_the_best_short-term_indicators_-673367) and [dynamic exits](https://tradingmarkets.com/recent/dynamic_exits_how_to_properly_exit_a_trade-641067) | Author reports multi-year simulated short-term reversal studies and indicator-based exits. | Slower ETF research lead, separate from Scalper; not implemented as a new runtime strategy here. |
| [Alvarez IBS study](https://alvarezquanttrading.com/blog/internal-bar-strength-for-mean-reversion/) | Author's historical all-trades experiment, explicitly not a portfolio. | Useful methodology; no year-long independently reconciled live Atlas bot record. |
| [AQR trend following](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing) | Long historical multi-market trend research with modeled costs. | Broad trend evidence, not a high-win intraday scalper replication. |
| [SetupAlpha mean reversion](https://setupalpha.com/products/mean-reversion-2025-realtest-strategy) and [validation](https://setupalpha.com/pages/strategy-validation-process) | Seller claims live tracking since May 2024 plus long backtests. Inspected pages do not establish an independently reconciled broker record. | Research lead only. No purchase or proprietary-rule copying. |
| [CanJee bot](https://github.com/CanJee/trading-bot) | Code and operational feature descriptions; inspected README does not establish a year-long profitable trade ledger. | Architecture claims are not performance evidence; not installed or executed. |

Do not choose a strategy by headline win rate. Profit after costs, losing-trade
size, exposure, drawdown and execution realism must be assessed together.
