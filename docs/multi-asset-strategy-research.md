# Five-market strategy research and implementation

Research date: October22,2026. These are candidates for testing, not established
Atlas winners. There is no verified universal highest-win strategy for each
market. No rate from a creator, paper or index is assigned to Atlas.

## Candidate shortlist

| Market | Candidate and precise scope | Evidence and limits | Atlas implementation |
| --- | --- | --- | --- |
| Equity momentum | Five-minute opening range, bullish opening candle, completed breakout then retest/reclaim, enter following bar. Frozen five-bar-low stop. | [Swiss Finance Institute ORB study](https://www.sfi.ch/en/publications/n-24-98-a-profitable-day-trading-strategy-for-the-u.s.-equity-market) covers7,000+ stocks over2016–2023 and reports stronger costed portfolio results for stocks in play. It does not establish a high win rate for this Atlas retest adaptation. | `OPENING_RANGE_RETEST` in the existing causal minute replay. Eight-bar minimum permits early evaluation; full-session data starts09:30 Eastern. |
| Scalper | Price reclaims rising session VWAP after a pullback, rising20-bar SMA, bullish close through prior candle high. | Experimental Atlas hypothesis. No audited high-win record was established. [Limit-order research](https://arxiv.org/abs/1607.00454) studies mean-reverting reference prices, not a portable retail win rate. Queue/latency assumptions prevent importing market-maker results. | `TREND_VWAP_PULLBACK`, compared at0.75R,1R,1.5R with existing modeled/doubled costs, time exits and session close. |
| Crypto | BTC/USD and ETH/USD spot only; completed1h pullback/reclaim with8/21 EMA agreement and completed4h trend confirmation. Stop at the three-bar structural low. | [Liu and Tsyvinski](https://www.nber.org/papers/w24877) identify crypto momentum. Hourly timing and these entry/stop rules are Atlas hypotheses, not a reproduction. | `crypto_pullback`; research setup only, independent of current spot-paper worker. |
| Futures | Actual dated contract, completed5m pullback/reclaim in8/21 EMA trend; long/short; structural stop buffered one supplied tick. Require15minutes remaining before declared session end or expiry. | [AQR trend-following research](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing) provides broad trend evidence. Its horizons are much longer than this intraday adaptation; it does not validate this signal or a high hit rate. | `futures_pullback`; requires tick, multiplier, expiry and explicit session bounds. Actual contract and roll data remain necessary. |
| Options | Defined-risk European cash-settled put credit spread: short higher strike, long lower strike, matched underlying/expiry/settlement/multiplier. | [OCC/OIC mechanics](https://www.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread) define spread payoff. [Cboe CNDR methodology](https://cdn.cboe.com/api/global/us_indices/governance/CNDR_Methodology.pdf) is an implementable four-leg benchmark with protective wings; a two-leg directional spread is different. Neither proves an Atlas win percentage. | `PutCreditSpread` quoted-side cost/payoff model only. Strike/delta/expiry selection, historical chain replay, atomic multi-leg execution and portfolio allocation are not implemented. |

The ORB source uses volume-based universe selection and ATR-derived stops.
Atlas's retest entry, structural stop and ranking choices change that system.
No source RVOL cutoff, spread veto, price ceiling, short-stock authority or
leverage setting is copied into the runtime. Existing spread/RVOL participation
remains adaptive; agreed hard safety controls retain their authority.

A [QuantConnect implementation](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/)
provides code and a2016 result, not independent proof of a persistent high win
rate. Its parameter search is visible research, not an untouched holdout. The
original ORB's portfolio profit claim must not be represented as a trade hit
rate. The Atlas variants need point-in-time mover selection and delisted names;
choosing today's winners would introduce selection bias.

For crypto, a [2026 matched-market study](https://arxiv.org/abs/2608.21888)
reports widespread15m reversal but a gross edge below benchmark spot capture
costs. Its statement that90% of pairs show significant reversal is **not a90%
trade win rate**. This supports testing slower, larger moves before fast crypto
scalping. Atlas's current spot signal defaults model0.1% fees plus0.1% slippage
per side, approximately0.4% round trip before spread; those are assumptions
until reconciled with actual venue execution costs. No derivatives, funding,
margin shorts or perpetual-contract results are substituted for spot data.

For futures, use contract-specific exchange data and the actual fee, margin,
roll and session model. [CME's Micro E-mini overview](https://www.cmegroup.com/education/courses/micro-e-mini-futures/micro-e-mini-futures-products-overview)
explains why tick/multiplier sizing matters; an equity-price backtest cannot
stand in for futures contract P&L. Monthly trend evidence does not make a
five-minute strategy proven. MES/MNQ are intended research roots, not an
instruction to execute a continuous-chart symbol.

## Mechanics and review controls

`multi_asset.py` has no account, network, broker, ledger or activation API.
Crypto rejects gaps, future higher-timeframe candles and stale/misaligned bars.
Futures rejects generic/continuous contract symbols, wrong tick geometry,
session-spanning history and insufficient remaining session/expiry time.
These are source/structure checks; session/halt/account authorization still
belongs to the existing trusted execution services.

The options model uses short-leg bid and long-leg ask for entry, then short-leg
ask and long-leg bid for liquidation, with explicit per-leg fees/slippage.
It reports full-loss and intermediate terminal outcomes, not just expiring
winners. Matching settlement identifiers distinguish AM/PM settlement. American
physical-delivery contracts are excluded because assignment is not modeled.
Terminal loss bounds are not a promise about earlier liquidation at wide quotes.
The two-outcome break-even calculation assumes full-profit winners and
full-loss losers, not a probability estimate. Delta is never treated as a
measured win rate. Cboe's hypothetical index and its midpoint methodology are
not assumed to be executable fills for Atlas.

The current Options PAPER ledger supports long options, not these spreads;
its guards are not bypassed. Futures/Options desktop feeds and execution remain
unconnected. Crypto's separate spot-paper system is not switched to this
prototype. This commit changes research surfaces only.

`review_gate.py` freezes engineering targets before subsequent testing:

- At least100 closed trades across at least three unseen date windows.
- At least60% winners, with a descriptive95% Wilson lower bound above50%.
- Positive net expectancy at normal and doubled modeled costs; profit factor
  at least1.20; closed-trade drawdown no more than10R.
- Independently verified source, cost model, canonical replay, PAPER forward
  evidence and asset constraints (including funding/roll/settlement when relevant).

These thresholds are design targets, not established optimal parameters.
The gate validates supplied statistics/flags, not the underlying data; window
independence, sample provenance, portfolio correlation, open losses and tail
scenarios still need external audit. It cannot turn self-reported evidence into
an audited result. Even a pass returns only `ELIGIBLE_FOR_MANUAL_REVIEW` and
`execution_authorized=false`. No optimizer, automatic capital transfer or
production promotion is introduced.

The previously screened daily ETF variants remain outside promotion. Their
private validation outcomes are retained privately; no figures or source
capture files are published in this repository. They remain comparison records,
so unsuccessful tests cannot disappear from the selection history.

## Run mechanics and inspect required data

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.strategy_research.test_multi_asset tests.strategy_research.test_comparison tests.strategy_research.test_audit_stdlib -v
& .\.venv\Scripts\python.exe -m app.strategy_research.plan
```

The plan lists the five candidates, precise data requirements and implementation
readiness. Optional `--evidence` accepts a bounded local JSON file matching
`ReviewEvidence`, with decimal statistics represented as strings. It does not
fetch data, audit submitted metrics or start trading.

Next data work: replay equity/scalper candidates against saved intraday captures;
collect venue-specific BTC/ETH hourly and completed4h history; obtain actual
futures contract series with roll metadata; obtain timestamped historical
cash-settled option chains/settlement before implementing spread entry selection.
The uploaded daily ETF bundle cannot establish performance for these markets.

## Export existing intraday evidence

`tools/capture-intraday-research.ps1` uses only standard-library verification and
read-only SQLite access. It fetches reviewed source into a disposable worktree,
exports saved market records and selects a Desktop ZIP. It never switches the
runtime checkout, imports its GUI/scientific stack, launches Atlas or requests
new provider data. This remains usable when an unrelated scientific DLL is
blocked by Windows policy. Native PowerShell verification still requires the
workstation run.

Defaults: the normal equity forward-capture path and root-level
`crypto-completed-bars.sqlite3`. Supply `-EquityCapture`/`-CryptoCapture` for
custom paths. Missing sources are listed and never created. The default window
is45days; a maximum50,000 saved records and32MiB per source is enforced.
Truncation/rejections/source errors are explicit. Missing crypto volume remains
null; it is never guessed. Equity timestamps are recorded observations, not
independently established durable availability. Crypto interval captures are
M1/M5/M15; any1h/4h research aggregation must use complete contiguous source
blocks and retain conservative availability, with gaps rejected.

The archive contains only allowlisted OHLCV, market timestamps, quoted bid/ask,
quality flags and hashes. Account/fill tables and credentials are excluded;
quote-path campaign/lifecycle identifiers are hashed. Source hashes are transfer
integrity checks, not proof that provider data is correct. No archive or private
result figures are committed to GitHub. Full-session coverage, capture selection
bias, quote gaps, latency and actual contract data remain review requirements.

```powershell
$Repo = "$env:USERPROFILE\WebullAITrader"
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw "Fetch failed." }
$Script = Join-Path $env:TEMP "capture-intraday-research.ps1"
git -C $Repo show FETCH_HEAD:tools/capture-intraday-research.ps1 |
    Set-Content -LiteralPath $Script -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw "Script extraction failed." }
& $Script -Repo $Repo
```

### Saved-data export version2

The equity reader now uses bounded integer-primary-key sequence windows instead
of a combined record-type/date query that could sort a large matching history
before producing its first rows. The SQLite query plan is checked to use the
sequence range without a temporary sort even when the source has a type/time
index. No index is created and no source table is modified. The scan visits at
most250,000 sequence slots in2,000-slot pages and retains the existing50,000-row,
32MiB and read-time bounds. Gaps/pruning can exhaust the slot budget; the manifest
reports scan limits and truncation rather than declaring complete coverage.

Failures now include a fixed stage and SQLite's numeric/symbolic error code
when available, without raw exception messages, SQL contents, host paths or
record payloads. This distinguishes interruption, locking and schema errors on
the next workstation run; an old generic OperationalError cannot establish
which occurred. A failed source produces PARTIAL_SOURCE_EXPORT or
SOURCE_EXPORT_FAILED and a nonzero CLI exit. The wrapper still selects a produced
diagnostic ZIP before reporting the failure, so it remains reviewable. Missing
sources remain explicit; they are not synthesized. The shared ten-second query
progress deadline and read-only connection guards are unchanged.


Exporter v3 preserves completed equity pages when a later page reaches the existing
SQLite progress deadline. The interrupted page is discarded, the connection closes,
and only completed pages pass through the normal allowlist and validation. The source
remains `SOURCE_ERROR` with `SQLITE_INTERRUPT`, `partial_read`, `scan_limited` and
`truncated` flags; the command still returns nonzero. A partial ZIP is for coverage
review only. No retry, deadline extension or source mutation occurs. If no usable
completed page exists, the original source-failure behavior remains.


Exporter v4 uses the existing `capture_type_time(record_type,timestamp,sequence)`
index when its columns match. It reads each market-record type separately, with
keyset pages and half the result allowance per type (25,000 rows each at defaults).
It avoids spending the sequence-scan allowance on unrelated runtime events. Date
filtering remains offset-aware; cursor ordering follows SQLite's exact timestamp
text/sequence index order. Text timestamp ordering across mixed offsets is not a
promise of chronological selection when truncated. Indexes are never created or
modified. Missing/incompatible indexes use the bounded sequence fallback. The same
10-second connection deadline, aggregate byte cap and completed-page timeout
handling remain. Counts/truncation are explicit; this does not guarantee older
history exists or that captured data meets replay requirements.
