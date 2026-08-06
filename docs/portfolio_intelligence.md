# Portfolio intelligence

## Architecture and sources of truth

Portfolio intelligence is an observational, broker-neutral projection in
`app/portfolio_intelligence`. It is downstream of the authoritative account,
position, working-order, fill, decision, and market-price projections. It never
queries a broker and never writes calculated values back to orders, fills,
journals, or positions.

The production and replay paths use the same ordered pipeline:

```text
persisted runtime events
  -> order / position / decision projections
  -> portfolio intelligence projection
  -> immutable PortfolioIntelligenceSnapshot
  -> application state -> Mission Control
```

The runtime event stream supplies fill identity, marks, and decision metadata.
The existing position and order read models remain authoritative for current
holdings and working orders. Broker reconciliation can supply the generic
`PortfolioAccount`; Webull fields do not enter this domain. Replaying the same
events into a fresh projection rebuilds the same snapshot. Fill IDs and ordered
source sequence numbers prevent duplicate trade counting.

## Exposure formulas

All arithmetic uses `Decimal`.

- Position market value = signed quantity × current mark.
- Long exposure = sum of positive market values.
- Short exposure = sum of absolute negative market values.
- Gross exposure = sum of absolute market values.
- Net exposure = sum of signed market values.
- Position weight = absolute market value ÷ account equity.
- Cash percentage = cash ÷ account equity.
- Buying-power utilization = gross exposure ÷ (gross exposure + buying power).
- Pending-order exposure = sum of remaining quantity × determinable order price.
- After-fill exposure applies each signed working-order notional to its symbol.

Zero equity yields Unknown weights rather than division errors. If any held
position lacks a mark, complete portfolio exposure and concentration totals are
Unknown; the position is never treated as zero. After-fill exposure is Unknown
when any working order has no determinable price.

## Concentration

Largest, top-three, and top-five allocation are sums of absolute
equity-relative position weights. HHI is the sum of squared shares of gross
market value, so it ranges from near zero for diffuse portfolios to one for a
single position. Long/short imbalance is `abs(long - short) / gross`.

Strategy, asset-class, and sector concentration use shares of gross market
value. Missing strategy metadata is `Unattributed`. Sector concentration is
Unknown unless every held position already has reliable sector metadata; no
external lookup or hardcoded classification is used.

## Correlation

The broker-neutral `CorrelationAnalyzer` interface accepts held positions and
historical `PriceObservation` series. The default implementation computes
consecutive simple returns, aligns returns by observation timestamp, and uses
pairwise Pearson correlation. It uses at most the configured lookback and
requires the configured minimum overlapping observations (default 20). Constant
series and insufficient pairs are excluded. It reports the greatest absolute
pair, signed average pair correlation, pairs at or above the absolute high
threshold, and the gross-value percentage represented by symbols in those
pairs. Correlation is presentation-only and never affects decisions or orders.

The interval setting documents the expected source-series interval. This phase
does not resample or invent missing bars.

## Performance and trade grouping

A trade is a symbol's flat-to-flat interval in the persisted fill sequence.
Fills are sorted by timestamp and fill ID and deduplicated by fill ID. A trade
opens when signed quantity leaves zero and closes when it returns to zero or
crosses through zero. Partial exits remain in the same trade. A reversal closes
the old trade and begins a new interval at the reversal timestamp. A closed
trade is classified only when all contributing realized-PnL facts are present.
Position disappearance is never used to infer closure.

- Cumulative realized PnL = sum of unique persisted fill realized-PnL facts.
- Gross profit/loss = sums of positive/negative closed-trade PnL.
- Win/loss rate = winning/losing trades ÷ classified trades.
- Profit factor = gross profit ÷ absolute gross loss; zero loss is Unknown.
- Average win/loss = respective total ÷ respective count.
- Expectancy = closed-trade PnL ÷ closed-trade count.
- Holding period = close time − open time, averaged over closed trades.
- Return on equity = `(ending equity - starting equity) / starting equity`.
- Drawdown = `(running peak - equity) / running peak`.

Maximum drawdown is the greatest replayed drawdown; current drawdown is the last
point's drawdown. Daily realized PnL uses the configured timezone and shifts the
calendar boundary by the configured hour. Daily unrealized and daily total PnL
remain Unknown until a reliable prior-boundary valuation exists.

## Attribution

Realized and unrealized PnL are attributed independently by symbol, strategy,
decision type, asset class, and session using only persisted metadata. Missing
strategy, decision, or session metadata is `Unattributed`; it is never guessed.

## Risk-budget status

Configured gross exposure, absolute net exposure, largest position, daily loss,
drawdown, open-position count, and buying-power-utilization limits are classified
as `Within Limits`, `Approaching Limit`, `At Limit`, `Exceeded`, or `Unknown`.
Approaching begins at the configured warning percentage (default 80%). Missing
metrics or limits are Unknown. The overall classification is the most severe
known classification. These statuses do not block, resize, or change orders.

## Mission Control and events

Portfolio Overview retains the dashboard layout and adds net exposure and
current drawdown. The Portfolio Intelligence tab presents largest-position,
top-five concentration, highest-correlation, win-rate, profit-factor, and risk
budget observations. AI Thinking may display only deterministic observation
rules; it does not generate advice.

`MeaningfulChangeDetector` emits structured observations only for exposure
bucket crossings, concentration-class changes, new maximum drawdown, risk-budget
state transitions, newly high-correlation pairs, and completed-trade/performance
changes. Ordinary price ticks within the same state are suppressed.

## Configuration

Safe defaults and environment keys are:

| Setting | Default | Environment key |
|---|---:|---|
| Correlation lookback | 60 | `PORTFOLIO_CORRELATION_LOOKBACK` |
| Correlation interval | `1d` | `PORTFOLIO_CORRELATION_INTERVAL` |
| Minimum observations | 20 | `PORTFOLIO_MINIMUM_CORRELATION_OBSERVATIONS` |
| High-correlation threshold | 0.80 | `PORTFOLIO_HIGH_CORRELATION_THRESHOLD` |
| Concentration warning / critical | 0.50 / 0.75 | `PORTFOLIO_CONCENTRATION_WARNING_THRESHOLD`, `PORTFOLIO_CONCENTRATION_CRITICAL_THRESHOLD` |
| Risk warning percentage | 0.80 | `PORTFOLIO_RISK_BUDGET_WARNING_PERCENTAGE` |
| Reporting timezone | `America/Chicago` | `PORTFOLIO_PERFORMANCE_TIMEZONE` |
| Trading-day boundary hour | 0 | `PORTFOLIO_TRADING_DAY_BOUNDARY_HOUR` |

Values are validated for finite bounded decimals, observation/lookback
consistency, valid IANA timezone, and a 0–23 boundary hour.

## Restart behavior and limitations

The snapshot is a replayable read model, not a second trading ledger. Restart
recovery rebuilds fills, attribution, price history, trades, and drawdown from
persisted facts, then reconciles current positions through the existing broker
path. No derived analytics are written into source records.

Limitations: daily unrealized PnL requires a future persisted boundary baseline;
working orders without prices cannot produce projected exposure; sector metrics
require existing reliable metadata; and correlation requires sufficiently
aligned stored observations. All unavailable values render as `Unknown` or `—`.
