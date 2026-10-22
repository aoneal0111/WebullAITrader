# Atlas high win-rate strategy research

Research date: 2026-10-16. First implementation: offline candidate screening.
Session-aware scalp admission and setup attribution repairs are now included;
no strategy activation or profit validation has occurred. The user's objective is materially more winning positions and
better net profits. No advertised win rate is an Atlas performance estimate.

## Evidence shortlist

The search covered creator material, original strategy publications, research
papers, broker examples, implementation research, and independent replications.
Search hits are not equivalent to reviewed papers; no claim is made that
hundreds of sources were fully reviewed.

| Source | Reported evidence | Decision |
| --- | --- | --- |
| [Connors Research, CMT June 2013, pp.27–33](https://cmtassociation.org/wp-content/uploads/2025/08/june2013.pdf) | ETF Bollinger(5,1) pullback variant: 92.23% winners, 103 trades, 4.55 days average hold; January 2006–April 2013. Selected from a parameter search, with a next-day limit entry. | Highest reproducible headline in this review; swing research only. A conservative daily limit-entry adaptation is implemented for screening; modern ETF replication is still required. |
| [Larry Connors, Improved R2](https://tradingmarkets.com/recent/the_improved_r2_strategy_84_correct_with_just_6_rules_-674361) | 84.31%, 102 simulated trades, 5.76 days average hold, 1995–2006. | Implement daily hypothesis with explicit deviations; do not inherit the rate. |
| [ConnorsRSI original guide](https://alvarezquanttrading.com/wp-content/uploads/2016/05/ConnorsRSIGuidebook.pdf) | Selected variants near 80%; 2001–September 2012. Guide says slippage/commission omitted. | Further swing candidate; distinct from RSI(2). No headline copied into Atlas. |
| [Cesar Alvarez: entry timing](https://alvarezquanttrading.com/blog/mean-reversion-entry-timing/) | 2007–2017 comparisons: roughly 65% winners with RSI exits versus 29% with an N-day-low exit in one comparison. | Evidence to test entry and exit jointly. “Intraday pullback” describes an entry into a multiday strategy. |
| [Cesar Alvarez: stops and scaling out](https://alvarezquanttrading.com/blog/adding-stops-and-scaling-out-to-a-mean-reversion-strategy/) | Tested combination worsened results in his example. | Earlier partials are a hypothesis, not a universal repair. Keep Atlas's hard risk protection. |
| [StockCharts RSI(2)](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2) | Public trend-filtered pullback rules; no transferable current win-rate guarantee. | Implement daily baseline. |
| [Alpaca mean-reversion tutorial](https://alpaca.markets/learn/how-to-build-backtest-mean-reversion-strategy-with-alpaca) | Implementation example using statistical deviation and backtesting. | Engineering reference, not verified high-win performance. |
| [QuantConnect ORB implementation](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/) | Recreation reports 2.396 Sharpe for 2016; not a high-win-rate claim or full-period reproduction. | Lower priority for the user's win-rate objective. |
| [Original SPY intraday momentum study](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172) and [independent replication](https://github.com/PazSheimy/spy-intraday-momentum-oos) | Replication reports Sharpe 1.34 in-sample, 0.39 on newer dates; hit ratios 44.8% and 45%. Results not rerun here. | Strong reason to require unseen dates, not just an impressive backtest. |
| [QuantConnect pairs example](https://www.quantconnect.com/research/15300/pairs-trading-with-stocks/p1) | Reproducible pair-selection and divergence framework. | Defer: shorting, two-leg execution, and a different portfolio model. |
| [ATrain's IV Wall material](https://atraintrading.com/opt-in-602614) | Public educational alerts and proprietary tools; [26/30 title claim](https://www.youtube.com/shorts/WulK1xX50jM). | No audited sustained live win rate established. Exact formula/data access unavailable. |
| [Lil Fish / Oceanfront](https://www.youtube.com/watch?v=XLMG6-SATRk) | Advertises 10,000+ strategies and millions of trades. | Raw strategy set and independent live verification not established. |
| [Bailey et al., backtest overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) | Formal treatment of strategy selection bias. | Record every tested variant; reserve a final untouched holdout. |

The 92% ETF result is a historical selected variant, not an audited live
track record. It also used a different holding period, universe, entries and
exits from Atlas. An intraday strategy cannot inherit a daily strategy's rate.

## Existing system findings

Baseline branch: `fix/research-backlog-footprint-circuit`, commit
`6f75270b201099b14de48f0bf00ce84afbe71f64` (PR #107 merge).
Current repository includes Quick Scalper, opportunity ranking, profit
retention, historical replay, walk-forward, and experiment coordination.
Older claims that no dedicated Quick Scalper exists are stale.
The existing `asset_modules.strategy_comparison` compares caller-supplied
paired episode outcomes; it does not generate these new entry hypotheses.
Use it for frozen exit-policy comparisons on common episodes, not to compare
unrelated candidate trades by pretending their entry episodes match.

The generic backtesting runner replays caller-supplied AI responses/intents;
the exit comparator evaluates supplied entry indices. Neither is an automatic
implementation of the new candidate entry/exit hypotheses. The new package is
an intentionally small screening surface using the existing Wilder RSI
implementation. Its OHLC assumptions are explicit; production-equivalent
execution still belongs in the canonical replay/gateway boundaries.

The available 2026-10-14 loss-diagnosis export selects the three largest closed
losses per equity engine, six losses total. It is **not** the all-trades ledger,
so neither overall win rate nor which algorithm to retire can be inferred
from that selected sample. It lacks the complete comparable bar histories
needed for this screening command. Do not commit private capture contents.

## Implemented hypotheses

| Candidate | Entry | Exit |
| --- | --- | --- |
| RSI2_DAILY | Daily close above SMA200 and Wilder RSI2 below10. | RSI exit: close above SMA5, execute next open. |
| IMPROVED_R2_DAILY | Above SMA200; RSI2 from two bars ago below65, then two successive lower readings. | RSI2 above75, execute next open. |
| BOLLINGER_PERCENT_B_DAILY_LIMIT | Above SMA200; Bollinger(5,1) %b below -0.2 for three closes; 21-day average volume at least125,000 and minimum50,000. Next-session limit2% below setup close. | %b above1, execute next open; structural stop and holding limit retained. |
| RSI2_VWAP_PULLBACK | Intraday RSI2 rebounds from below10, price above rising session VWAP and above prior close. | Fixed reward, stop, time limit, or session end. |
| VWAP_BAND_REENTRY | Prior close below prior session VWAP minus2 weighted standard deviations; bullish close returns inside current lower band. | Same fixed-risk exit comparison. |
| FAILED_BREAKDOWN_RECLAIM | Sweeps prior20-bar low and closes above it with a bullish candle. | Same fixed-risk exit comparison. |
| BREAKOUT_CONTROL | Close-confirmed break over prior20-bar high. | Same fixed-risk exit comparison. This is not a reproduction of Warrior. |

Intraday definitions are experimental Atlas hypotheses, not published high-win
systems. Three reward variants (0.75R, 1R, 1.5R) are reported for each intraday
candidate. Daily variants use indicator exits, no fixed target. The Bollinger limit expires
after one session and requires strict low-price penetration; a touch gets no
fill. Fills receive no favorable opening-gap improvement and still pay modeled
costs. Population standard deviation is explicit. Unlike the publication, Atlas
retains its structural stop, delays the close-confirmed exit to the next open,
and applies a maximum holding period. This is an adaptation, not a reproduction
of the 92.23% result. The caller must supply a verified eligible equity ETF
universe (no leveraged, inverse, commodity or bond ETFs); CSV prices alone do
not establish eligibility. Every entry
uses a frozen five-bar-low stop buffered by 0.05%. That stop, next-open daily
execution and maximum holding period modify the published systems.

No martingale, averaging down, unlimited stop, spread veto, or RVOL veto is
introduced. This command has no broker, order dispatcher, feed, or activation
path. Execution authorization is always false.

## Run offline

First obtain the complete active-campaign outcomes through the existing
read-only lifecycle reconciler (no separate learning-journal ingestion):

```powershell
Set-Location "$env:USERPROFILE\WebullAITrader"
& .\.venv\Scripts\python.exe -m app.strategy_research.audit |
    Set-Content "$env:TEMP\atlas-equity-strategy-audit.json"
if ($LASTEXITCODE -ne 0) { throw "Equity audit failed." }
```

Pass `--ledger` if the active execution ledger uses a nondefault path.
This exports every reconciled closed lifecycle in the active campaign and all
exclusion counts, rather than selecting only losses. It cannot report older
campaigns or reconstruct missing historical candles. Summary groups preserve
engine, frozen policy version and recorded strategy family.
`tools/capture-strategy-audit.ps1` automates the Windows verification/export in
a separate detached worktree using the existing virtual environment. It does
not switch the runtime checkout or launch Atlas. Its ZIP contains only the
audit and reviewed revision, not the source database, environment or credentials.
The capture verification runs five standalone `unittest` checks for the audit,
not repository pytest/conftest or strategy-performance tests. The canonical
equity reconciler imports bounded read-only SQLite helpers directly from
`app.read_only_sqlite`; the old capture-export module retains those exports
for compatibility. The audit needs only Python's standard library and does
not load pandas, NumPy, Qt or the trading runtime. A subprocess test verifies
that export works with those imports rejected and site packages disabled.
The synthetic ledger fixture closes its connection explicitly; the SQLite
transaction context alone does not release a Windows file handle. A regression
check holds a reference to that connection and verifies it is already closed
before the fixture returns, then deletes the temporary database.
This dependency isolation does not repair a blocked pandas installation or
change Windows Application Control. Strategy comparison/full-runtime checks
still require their normal dependencies to load under the host policy.

CSV columns:

`symbol,opened,closed,open,high,low,close,volume,session_end`

One canonical symbol per file. Timestamps must include UTC offsets. OHLCV must
use consistent adjustment and corporate-action treatment. For minute input,
provide complete, contiguous regular sessions starting at 09:30 Eastern, with
the actual session close supplied explicitly, including early closes. Missing
minute bars fail rather than creating synthetic tradable candles. Use daily
input only for the daily hypotheses. At least 200 preceding daily bars are
needed for those candidates; warmup history is not a trade.

```powershell
Set-Location "$env:USERPROFILE\WebullAITrader"
& .\.venv\Scripts\python.exe -m unittest tests.strategy_research.test_comparison -v

# Replace paths, split date, and modeled costs with your data/assumptions.
& .\.venv\Scripts\python.exe -m app.strategy_research C:\Temp\atlas-bars.csv `
    --timeframe minute `
    --split-at "2026-10-01T00:00:00-04:00" `
    --adverse-bps-per-side 10 `
    --fee-per-share-per-side 0.005 `
    --max-hold-bars 10 |
    Set-Content C:\Temp\atlas-strategy-screening.json
if ($LASTEXITCODE -ne 0) { throw "Strategy screening failed." }
```

Costs above are illustrative, not measured Webull execution costs. Both cost
arguments are mandatory. Spread plus slippage is one adverse per-side bps
allowance, with a separate per-share fee. A doubled-cost run is reported too.

Signals use completed history. Market-entry candidates fill the next bar open;
the daily Bollinger candidate waits for penetration of its next-session limit.
`entry_time_precision=BAR_WINDOW` labels intrabar limit entries: `entry_at`
identifies that bar’s start, not an observed fill instant. Stop gaps use the
worse open; ambiguous OHLC bars take the stop before the target. Only one
position per candidate is open at a time. Zero-volume entry bars do not fill.
Intraday positions exit at known session close; missing sessions are not
fabricated. Time exits are preplanned by bar count; indicator exits use next
open. Evaluation boundaries force a marked close. Bars remain OHLC proxies:
target/stop event timestamps are recorded at the containing bar close, not
as observed tick timestamps. No quote queue, partial fills, halt/status,
settlement, or account portfolio is simulated here.

Report identity includes file, configuration and implementation SHA256s.
Every tested variant reports development, chronological validation and doubled
cost validation. Pre-split data may warm indicators but never seeds pending
orders or open positions across the split. Intraday splits require a session
boundary. Open histories should include independent provider quality checks.

Metrics count complete strategy exits after modeled costs, including breakevens
in the denominator. Marks forced solely by an evaluation/file boundary are
reported separately and excluded from hit rate, profit factor, expectancy and
closed-trade drawdown. P&L per share and risk-unit profit factor/expectancy are
screening quantities, not realized account profit. Closed-trade drawdown omits
intratrade equity drawdowns. Wilson intervals are descriptive and assume
independent trades; correlated trades require session-block resampling.

## Replacement and retirement criteria

1. Reconcile the complete filled-position ledger, grouped by lifecycle and
   engine, including all partial exits/fees. Separate unfilled proposals from
   actual outcomes. Verify the reported sub25% rate on this basis.
2. Compare entry and exit changes on identical capture periods. Attribute
   loss to setup, execution, session boundaries and protection behavior before
   declaring an algorithm the cause.
3. Prioritize validation win rate at least60%, positive net expectancy and
   acceptable risk. These are objectives, not promised outcomes. Do not accept
   a high rate manufactured by outsized losses or omitted open positions.
4. Require adequate independent sessions and a meaningful sample (initial
   target: at least100 validation positions), positive net performance under
   execution-cost stress and stability across dates/symbols. The count alone
   does not establish significance. Record all experiments and adjust for
   multiple testing. Keep final holdout dates unseen until candidates freeze.
5. Reproduce survivors in canonical quote/tick replay with the same hard risk,
   tradability, freshness, ownership, session and exposure controls as Atlas.
   Test portfolio sizing and simultaneous candidates, then PAPER forward test.
6. Disable losing routes by explicit configuration in a reviewable change.
   Delete unreachable implementation only after references, replay parity and
   protection dependencies are checked. No production deletion is justified
   by the selected six-loss export alone.

Remaining work: all-trades audit, complete historical data adapter,
production-equivalent replay integration, limit-entry ETF Bollinger variant,
cost/partial-exit comparisons for current engines, portfolio validation and
PAPER observation. This first change does not finish the requested overhaul.


## October 22 execution repairs and audit findings

The private campaign audit showed weak aggregate results, while legacy strategy
families and policy versions were UNKNOWN. Those outcomes do not establish
which current setup/version should be removed. A reviewed loss crossed a
liquidity-session boundary after a late scalp entry. This supports fixing entry
timing; it does not prove a future win rate or justify capping simulated losses.
Detailed private campaign figures and individual trade diagnostics are omitted.

Quick Scalper now requires enough time in the original premarket, regular,
or after-hours session for its configured maximum holding budget. The NYSE
calendar supplies actual early closes and Eastern DST. Runtime authorization
rejects stale session labels. Prepared entries persist the original last-entry
time and holding budget. The trusted controller rechecks this deadline after
review, and the PAPER gateway bounds working BUY remainders, including legacy
recovery, before matching. Filled-share SELL protection retains its authority.
This cannot ensure an exit during a quote outage and does not alter adverse
stop fills. Stream and bar scalp setups now retain separate frozen policy
identities instead of always appearing as the bar momentum family.

No blanket removal of extended-hours trading, higher stop tolerance,
averaging down, or reduction of existing hard safety controls is included.
The new daily Bollinger challenger, RSI2 and Improved R2 must earn promotion
on modern, costed, unseen ETF data and canonical execution replay. The supplied
outcome ZIP lacks the historical OHLC/quote paths needed to run those strategies
against the same opportunities. No new performance percentage is asserted.

## Capture and screen the daily ETF challengers on the workstation

`tools/capture-strategy-screening.ps1` fetches reviewed source into a disposable
worktree, verifies capture/screening mechanics and uses only the configured
market-data client to request daily history. It keeps `.env` resolution in the
existing repository; it does not copy credentials, access accounts, switch the
runtime checkout, place orders or launch Atlas. It removes only its temporary
source checkout when finished. Output stays on the Windows Desktop and Explorer
selects the ZIP, including when a partial capture produced diagnostic output.

The frozen sample is SPY, QQQ, IWM, DIA, EFA, EEM, XLF and XLV. Each request is
bounded to1,200 daily bars; there is one request per symbol, paced without
retries. Today's potentially incomplete daily bar is always excluded. The
provider timestamp's written date is treated as its daily session label, and
actual NYSE opens/closes are reconstructed. Missing trading dates, conflicting
duplicates, naive/future labels, short history and invalid OHLCV are rejected.
This date convention is explicit and must be reviewed against captured coverage;
it is not an assertion of observed fill times. Daily prices are provider
forward-adjusted; this is an additional point-in-time limitation.

The default split is midnight Eastern365 calendar days before capture, with
at least250 development bars and100 validation bars required. The first200
bars warm indicators. All three daily candidates are reported with a10-bar
maximum hold and double-cost stress; no optimizer selects a winner. The fixed
current ETF sample introduces selection/survivorship bias and correlated trades.
The bundle contains per-symbol CSVs, full screening reports, a summary and a
manifest with data/report/code hashes, dates, cost assumptions and sanitized
errors. No raw provider response, account record, `.env` or signed request is
included. An incomplete bundle is labeled INCOMPLETE and exits with code2.

```powershell
$Repo = "$env:USERPROFILE\WebullAITrader"
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw "Fetch failed." }
$Script = Join-Path $env:TEMP "capture-strategy-screening.ps1"
git -C $Repo show FETCH_HEAD:tools/capture-strategy-screening.ps1 |
    Set-Content -LiteralPath $Script -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw "Script extraction failed." }
& $Script -Repo $Repo -AdverseBpsPerSide "10" -FeePerSharePerSide "0.005"
```

The defaults10bps per side plus$0.005/share per side are illustrative modeling
assumptions, not measured brokerage costs. Pass explicit alternatives to record
other assumptions. OpenAPI market-data permission and working Python/SDK/calendar
dependencies are required. This does not repair Windows Application Control
blocking a scientific dependency and does not recommend disabling that policy.
The PowerShell wrapper still requires a workstation verification; provider
responses and credentials are deliberately unavailable in the cloud test run.

The current execution-repair revision passed both GitHub CI runs, including
all repository tests. A local research stress-test timeout was not reproduced
in CI; the local remaining tests passed in a fresh process excluding only that
stress case. Capture tests use synthetic data and establish data-handling
mechanics only, not an improved strategy win rate.
