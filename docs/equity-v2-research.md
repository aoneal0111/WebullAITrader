# Two fixed post-V1 equity research hypotheses

These experiments follow the independently reconciled V1 rejection. They are
new hypotheses designed after observing V1 results. They are not profitable
replacements, fresh holdouts, reproductions of author returns, or production
order producers. No qualification entry is added and the scalper stays paused.

## Source comparison

Primary reviews:
- https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/
- https://concretumgroup.com/backtesting-7-years-of-free-data-beat-the-market-an-effective-intraday-momentum-strategy-for-the-sp500-etf-spy/

The QuantConnect ORB example selects unusually active stocks from a much larger
liquid universe, uses ATR-related stops and exits at the stop or session close.
Its reported result is historical and the discussion contains concerns about
other years. Its return/Sharpe figures are not Atlas win-rate evidence.

The Concretum tutorial uses prior same-time opening deviations, VWAP, 30-minute
exposure decisions, long/short trading, volatility sizing and a separate opening
reversal component. It also accounts for dividends and order commissions. Its
portfolio implementation is materially different from Atlas V1 or these V2
adaptations. No third-party strategy source is copied or executed.

## Frozen V2 definitions

| Research ID | Entry/selection | Exit |
| --- | --- | --- |
| ORB5_ACTIVITY_RUNNER_V2 | AAPL/MSFT/NVDA/TSLA only. At 09:35, rank current opening-five-minute volume divided by the mean opening-five volume of 14 prior complete sessions; select top two, tie by symbol. Only bullish opening ranges arm a resting long breakout stop at the opening high. Fill at the worse of that level and a gap-up open, with modeled costs. Order remains eligible until the penultimate candle. | Opening low times 0.9995, fixed before entry. Stop or session close; no target and no 60-bar timeout. One entry per selected symbol/session. |
| NOISE_BAND_CLOCK_V2 | SPY primary, QQQ extension. Prior 14 sessions' same-minute mean absolute opening-relative move defines an upper boundary anchored to the higher of current open and prior close. Enter next open after a completed 30-minute clock close above both band and current session VWAP, through 15:30. | Session-low-so-far times 0.9995, fixed at the entry signal as emergency protection. Exit next open when a subsequent 30-minute decision fails band/VWAP, at emergency stop, or session close. No target, five-bar low stop or five-minute timeout. At most 12 entries/session; three-bar signal cooldown after exit. |

These differences are intentional research choices, not claims of source parity.
ORB ranks only four current stocks, applies no RVOL>1 cutoff, uses a structural
stop instead of the source ATR stop, and is long-only. It cannot reproduce a
historical top-20 out of 1,000+ stock universe. Opening volume is a research
ranking; production receives no new hard spread/RVOL gate. A zero opening volume
or missing prior-volume history cannot create a valid ranking.

Noise-band is long-only and unlevered, omits volatility sizing, overnight reversal
and corporate-action/dividend adjustments, adds a session-low hard stop, and
uses explicit next-open execution. It is not an exact tutorial or paper replica.
The first known stop cannot widen after entry; later lows cannot move it down.
All positions close in their original session. A stop-gap takes precedence over
a queued clock exit. ORB entry and stop touched within one candle are resolved
as a loss; OHLC does not establish their true event ordering or executable fills.

## Fixed local screen

The same saved input files are used, checked against the original report and
manifest hashes and source identities. The three original date windows are
retained only as already-seen period identifiers, including the old `holdout`
label. They cannot qualify as untouched V2 evidence. Source fingerprints,
experiment definition and all results travel in a small JSON summary.

The exact cost grid is frozen before V2 results: zero cost diagnostic; 1bp per
side plus $0.0035/share per side; 5bps plus $0.0035; 10bps plus $0.007. Every
symbol/policy/period/cost result is retained (72 cells, 24 aggregates). There is
no parameter optimization, best-symbol promotion or automatic qualification.
R statistics are per-trade initial-risk statistics, not a portfolio equity curve.
The screen may expose promising or failed hypotheses; it cannot prove capacity,
shared-capital drawdown, forward performance or actual execution costs.

The new implementation is mechanically tested, but does not yet have a separate
independent V2 reference engine. V1's independent replay agreement does not
transfer automatically to these new rules. A new prospective validation period,
independent V2 parity, execution calibration and production-adapter review remain
necessary even if every old period looks positive.

## Run without another download or API keys

Use `tools/run-local-equity-audit.ps1 -Mode ResearchV2` with its usual `-Repo`,
`-Revision` and optional `-ResearchRoot` parameters. It locates the original saved
history/report, uses a disposable reviewed worktree and runs the V2 tests first.
It does not switch the installed checkout, launch Atlas, call market-data APIs
or change trading authority. Upload only `UPLOAD_SUMMARY`, not the minute files.
The output is tested to stay under 100KB on the synthetic end-to-end fixture.

Direct invocation from an appropriate source checkout:

```powershell
& .\.venv\Scripts\python.exe tools/screen-equity-v2.py `
    --history 'CAPTURE_DIRECTORY\history' `
    --report 'CAPTURE_DIRECTORY\report.json' `
    --output 'NEW_SMALL_V2_SUMMARY.json'
```

No V2 strategy is listed in `RELEASE_PROMOTIONS`. The result always says
`execution_authorized: false`, `promotion: NONE`. Changing a strategy name or
showing an attractive old-period result cannot turn on BUY authority.
