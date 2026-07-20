# Webull AI Trader — Read-Only Foundation

This first version retrieves account balances and positions from a connected
Webull MCP server and prints a redacted JSON summary. It does **not** place,
preview, modify, cancel, or execute orders. Automated trading is not implemented.

## Requirements

- Python 3.13
- A connected Webull MCP server with account read access

## Setup

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `WEBULL_MCP_URL` in `.env` to the streamable-HTTP endpoint exposed by the
connected MCP server. Never commit `.env`; it is ignored by Git.

## Run

```powershell
python main.py
```

Account identifiers are redacted to their final four characters. Structured
application logs are JSON and filter sensitive field names.

## Test

```powershell
python -m pytest
```

Tests use mocked account data and do not contact Webull.

## Safety boundary

`WebullMCPClient` has a hard allowlist containing only `get_account_list`,
`get_account_balance`, and `get_account_positions`. Any other MCP tool name is
rejected locally before a request can be sent. Do not broaden this list without
a separate security review.

## Local AI prompt construction

`app.ai.prompt_builder` converts a `MarketSnapshot`, `MarketAnalysis`, and
`StrategyScore` into a validated `PromptPackage`. The package contains a strict
system prompt, a JSON user prompt, and versioned timestamp metadata. Construction
is dependency-free and local: it does not import an AI SDK, call a model, access
Webull, use the network, or execute orders.

```python
from app.ai.prompt_builder import build_prompt_package

package = build_prompt_package(snapshot, analysis, strategy_score)
print(package.to_json())
```

## Deterministic risk validation

`app.risk.evaluate_risk` validates a parsed `AIResponse` against the supplied
`MarketSnapshot`. Directional signals require at least 70% confidence, valid
stop-loss and take-profit levels, and reward:risk of at least 2:1. `HOLD` is
always allowed with a zero allocation.

The returned `max_position_percent` is a capped advisory portfolio percentage,
not a share quantity, not a notional brokerage amount, and not an order. The risk
package is deterministic and has no AI, network, Webull, account, or execution
access.

## Cash settlement and GFV boundary

`app.compliance.evaluate_sell_compliance` evaluates explicit sell requests using
recorded purchase lots and their funding sources. Cash-account lots funded by
unsettled proceeds or provisional deposits remain restricted until an explicit
settlement date; unknown funding always fails closed. Buying power,
available-to-trade, and provisional buying power are never treated as evidence
of settled cash.

The offline, injectable `SettlementCalendar` calculates applicable stock and
option settlement as T+1 market business day, skipping weekends and supplied
holidays rather than adding 24 hours.

If a request exceeds the GFV-safe quantity, it remains rejected. The reported
`safe_sell_quantity` is informational only: the module never resizes the request,
and a new explicit sell request is required. This package has no AI, network,
Webull, modal-warning, or order submission/modification/cancellation access.

## Order-compliance boundary

`app.order_compliance.evaluate_order_compliance` is the final deterministic
permission boundary before paper simulation. Its fixed check order validates
input models, the emergency kill switch, upstream risk and GFV approval,
freshness, symbol and market status, injected session boundaries, prices and
ticks, duplicate identifiers and fingerprints, long-position support, trade
count, combined daily loss, position concentration, and gross exposure.

Daily loss combines realized and unrealized P&L and converts a negative result to
a positive loss magnitude before enforcing both amount and percentage circuit
breakers. The enabled kill switch rejects every proposal and cannot be reset by
this validator. Unknown, malformed, future-dated, or stale state fails closed.

Market hours and status must be injected from a separately verified source; this
package contains no permanent holiday calendar or data lookup. Prices must match
the supplied tick exactly. An off-tick request remains rejected while optional
`lower_valid_tick` and `upper_valid_tick` values report both adjacent ticks. The
validator never selects either value.

Duplicate detection uses exact request IDs and deterministic fingerprints.
Concentration and gross-exposure checks use Decimal arithmetic and verified
prices; MARKET orders require an injected reference price. Any reported maximum
quantity or valid tick is informational only and requires a new explicit request.

Approval permits continuation to paper-execution simulation only. It is not live
trading permission. The package has no AI, Webull, MCP, network, order creation,
submission, modification, replacement, or cancellation access.

## Deterministic paper trading

`app.paper_trading.simulate_proposal` consumes an immutable `ProposedOrder` and
requires a matching approved `OrderComplianceDecision`. Approval permits paper
simulation only and never authorizes a live brokerage operation.

The caller injects a `PaperMarketQuote` and `PaperExecutionConfig`, including an
explicit `maximum_quote_age_seconds`; there is no hidden freshness threshold.
MARKET buys use ask and MARKET sells use bid, with no last-price fallback. LIMIT
buys cross only against ask and LIMIT sells only against bid. Uncrossed limits
are not queued or adjusted: the portfolio and equity curve remain unchanged and
only `PROPOSAL` and `NOT_FILLED` journal events are appended.

Portfolio transitions use Decimal average-cost accounting, require simulated
cash for purchases, prohibit short sales, and track cash, positions, realized
and unrealized P&L, and equity. The separate immutable equity curve must end at
the portfolio's exact equity before a fill; inconsistent history is rejected
rather than repaired.

Win rate, average winner, average loser, profit factor, and expectancy are
fill-level metrics. Every SELL fill with nonzero realized P&L is one outcome,
including a partial close; these are not round-trip trade metrics. Total return
and maximum drawdown use the separately supplied equity curve.

The simulator and append-only journal are deterministic and have no AI, Webull,
MCP, network, or live order creation, submission, modification, replacement, or
cancellation access.

## Deterministic historical replay

`app.backtesting` replays caller-supplied Decimal OHLCV frames one candle at a
time through the production indicator, strategy, prompt, response parser,
validator, risk, GFV, order-compliance, and paper-simulation pipeline. It does
not fork or duplicate those rules. AI responses, explicit order intents,
verified historical market state, bid/ask execution values, and session
boundaries are all supplied by the caller; no model or external data service is
called.

Analysis occurs on candle N and an approved proposal is eligible for simulation
against the caller-supplied execution values on candle N+1. Quantities and prices
are never inferred, resized, or repriced. Immutable replay events record every
pipeline stage.

Checkpoints use canonical JSON with exact Decimal strings, ISO-8601 timestamps,
a schema version, and fingerprints for candles, supplied responses, explicit
intents, and configuration. Resume rejects mismatched inputs. A continuous run
and a serialized pause/resume run therefore produce identical future state and
results. The engine includes no optimization, parameter search, live data,
broker synchronization, Webull, MCP, networking, or live execution.

## Deterministic experiments

`app.experiments` compares multiple immutable configurations over the exact same
historical frame tuple by calling the production `run_backtest` boundary. It does
not duplicate strategy, risk, GFV, compliance, or paper-execution rules. Each
experiment supplies its own AI-response and explicit order-intent datasets,
version labels, injected risk and compliance limits, and simulated initial cash.

Experiment runtime is deterministic historical replay duration plus event and
candle counts; it is not nondeterministic wall-clock benchmarking. Dataset and
behavioral-configuration fingerprints make comparisons reproducible. Notes are
descriptive and excluded from behavioral fingerprints.

Canonical JSON and plain-text reports compare return, drawdown, fill-level win
rate, profit factor, expectancy, fills, aggregate rejections, GFV rejections, and
order-compliance rejections. Reports never select a winner and perform no
optimization or parameter search. The framework has no broker, Webull, MCP,
network, AI-call, or live-execution capability.

## Deterministic walk-forward validation

`app.walkforward` creates rolling, expanding, or non-overlapping fixed-size
training/evaluation windows and orchestrates the public `run_experiments`
boundary. Training candles provide indicator history only: AI responses and
explicit order intents are filtered to evaluation timestamps, and no fitting,
optimization, parameter search, or winner selection occurs.

Every window starts from the experiment's original cash and empty simulated
state, so portfolios, settlement lots, pending proposals, and counters never
leak between windows. Aggregates are calculated separately per experiment.
Window returns are compounded, aggregate drawdown is the maximum observed window
drawdown, and win rate, profit factor, and expectancy retain the existing
fill-level realized-outcome definition.

Canonical JSON and deterministic text reports include source, training,
evaluation, combined-data, and configuration fingerprints. This orchestration
layer duplicates no indicator, strategy, prompt, AI parsing, validation, risk,
GFV, order-compliance, paper-trading, backtesting, or experiment logic and has no
AI calls, broker, Webull, MCP, networking, or live execution.

## Read-only simulation analytics

`app.analytics` is a deterministic, read-only consumer of completed backtests,
experiments, experiment suites, and walk-forward results. It never invokes a
runner or any indicator, strategy, AI, risk, compliance, execution, broker,
Webull, MCP, or network boundary. It performs no optimization, parameter search,
random sampling, winner selection, replay, or live execution.

Equity returns are Decimal fractions: `equity_t / equity_(t-1) - 1`; reports
label percentages separately. Daily, ISO-weekly, and monthly returns use the
last observation in each UTC calendar period without synthesizing missing
periods. Arithmetic volatility is the population standard deviation, dividing
by `n`. Downside deviation is
`sqrt(mean(min(return - minimum_acceptable_return, 0)^2))` across all
observations. Sharpe and Sortino use per-observation risk-free and minimum
acceptable rates. They are annualized only when an explicit positive
`annualization_periods` value is supplied. CAGR uses a 365.2425-day year and
Decimal exponentiation over positive elapsed time; unavailable or undefined
ratios are `None`, rendered as `N/A`.

Drawdown is the nonnegative Decimal fraction
`1 - current_equity / running_peak_equity`. An episode begins below a running
peak, reaches its lowest pre-recovery equity, and recovers at the first equity at
or above that peak. Average drawdown is the arithmetic mean of episode maximums,
including an unrecovered final episode. Durations are integer microseconds.

Exposure uses immutable portfolio snapshots recorded alongside equity points.
Gross exposure is `sum(abs(position market value)) / equity * 100`; net exposure
uses signed market value, and capital utilization uses gross position market
value. Time in market and averages are interval-duration weighted, not counts of
observations. Missing or misaligned observations are rejected or reported as an
explicit prerequisite. Exact holding duration and entry capital remain
unavailable because the simulator does not yet record authoritative lot-to-exit
allocation; analytics does not reconstruct or estimate them.

Trade outcomes are realized SELL fill events, so a partial close is an outcome
and expectancy is fill-level rather than round-trip expectancy. Win and loss
rates divide by non-breakeven outcomes; expectancy includes breakevens. Gross
loss is the absolute sum of negative P&L, profit factor is gross profit divided
by gross loss, and payoff ratio is average winner divided by absolute average
loser. Undefined denominators produce `None`.

Distributions use Decimal-only population formulas. Percentiles use deterministic
linear interpolation at position `(n - 1) * probability`; no NumPy, pandas,
SciPy, float conversion, implicit histogram bins, or parametric distribution is
used. Rolling metrics are observation-count based and require an explicit
positive window. Realized P&L groupings use UTC close month, ISO weekday, or
hour and are not labeled as returns.

Walk-forward analytics keeps every evaluation window independent and aggregates
each experiment ID separately. It never stitches independent equity curves;
continuous-portfolio risk ratios are therefore unavailable at that aggregate
level. Canonical JSON uses sorted keys, compact separators, exact Decimal
strings, ISO-8601 timestamps, explicit nulls, and no wall-clock timestamp.

## Deterministic Monte Carlo robustness analysis

`app.monte_carlo` resamples only observations retained by completed analytics.
It does not replay a backtest or call indicators, strategy, prompts, AI, risk,
GFV, order compliance, paper execution, brokers, Webull, MCP, or networks. It
does not optimize parameters, rank experiments, select a winner, or modify its
inputs.

Each immutable configuration supplies an integer seed, positive simulation
count, sampling mode, and exactly one source: ordered realized SELL-fill P&L
outcomes or point-to-point equity returns. Bootstrap sampling draws with
replacement. Permutation sampling uses a deterministic Fisher–Yates shuffle
without replacement. Both use an isolated, explicitly seeded SplitMix64-derived
integer generator and never access Python's global random generator. Rolling
block resampling is not enabled because the current configuration contract does
not contain an explicit block length; no hidden default is inferred.

For trade-outcome simulations, equity begins at recorded starting equity and
adds sampled realized P&L. For return simulations, equity compounds as
`equity = equity * (1 + sampled_return)`. Total return is
`ending_equity / starting_equity - 1`; maximum drawdown is the largest
`1 - equity / running_peak`. Profit factor, expectancy, win rate, and consecutive
streaks use the same positive/negative/zero observation conventions as the
completed analytics layer. Permutations therefore preserve aggregate outcome
statistics while allowing path-dependent drawdown and streaks to vary.

Metric summaries report Decimal mean, median, minimum, maximum, population
standard deviation, and linearly interpolated 5th, 25th, 50th, 75th, and 95th
percentiles. Probabilities are deterministic percentages from zero to 100 over
the completed simulations. Undefined profit factors remain unavailable rather
than being converted to zero. Experiment suites are ordered by experiment ID;
walk-forward results are ordered by window index and experiment ID and remain
independent.

Reports use canonical JSON with sorted keys, compact separators, exact Decimal
strings, stable ordering, and no wall-clock timestamp, plus a stable plain-text
format. Results with the same completed input, configuration, and seed are
identical.

## Deterministic scenario and stress testing

Original historical replay now records immutable authoritative market
observations in checkpoint schema version 3. Each observation may contain its
caller-supplied timestamp, symbol, OHLCV, bid, ask, session, market status,
observed slippage, volatility-regime label, and trend-regime label. Numeric
values are finite Decimals; timestamps are timezone-aware; OHLC relationships,
nonnegative volume, and `bid <= ask` are validated. Optional unavailable values
remain `None`. Observations are serialized in deterministic timestamp/symbol
order and propagated unchanged into completed analytics. Legacy checkpoint JSON
without this field loads with an empty observation tuple.

`app.stress_testing` consumes completed backtests or analytics, experiments,
experiment suites, and walk-forward results. It does not alter trades, fills, or
strategies and does not invoke AI, prompts, indicators, strategy, risk,
compliance, paper execution, brokers, Webull, MCP, or networks. It performs no
optimization, parameter search, winner selection, replay, or randomness.

Scenario filters are explicit and immutable. UTC date range, weekday, month,
hour, trade outcome, symbol, and equity-drawdown filters remain available for
legacy analytics where their source observations exist. Market-regime scenarios
match only caller-supplied labels: `BEAR`, `HIGH`, `LOW`, `TRENDING`, and
`SIDEWAYS`. Session, halt, and slippage scenarios likewise use only recorded
session, market-status, and observed-slippage fields. Crash and slippage
boundaries must be supplied explicitly; there are no hidden thresholds.

Gap, liquidity, and spread conditions fail closed as unavailable. The approved
history model contains no authoritative gap, liquidity, or spread measurement,
and the stress layer does not derive or relabel these from candle prices,
volume, bid, or ask. Missing regimes or other required fields also produce an
unavailable scenario with an explicit prerequisite warning rather than an empty
result.

Scenario equity, drawdown, trade, and risk calculations reuse the analytics
layer's Decimal formulas. A one-observation scenario has no authoritative
elapsed interval, so CAGR is unavailable. Scenario-aligned historical portfolio
snapshots are not recorded at every market observation; requested scenario
exposure therefore fails closed with a warning rather than being estimated.
Comparisons report absolute and percentage differences, direction-aware
better/worse/equal labels, and optional explicit adverse-difference thresholds.
Undefined values remain `None`.

Stress reports use canonical sorted-key JSON with exact Decimal strings and a
stable plain-text representation. They contain no runtime-generated timestamp,
and experiment and walk-forward adapters use deterministic experiment/window
ordering.

## Transport-isolated live execution architecture

`app.live_execution` defines a broker-neutral execution boundary, deterministic
order translation, immutable order states, partial-fill reconciliation,
append-only events, portfolio synchronization, and stable reports. It imports no
AI, indicators, strategy, analytics, Monte Carlo, stress testing, risk, or
compliance modules and cannot invoke any upstream component.

The existing `TradingDecision` and `OrderComplianceDecision` remain analysis and
paper-simulation contracts. Neither authorizes a live order. Every broker
mutation requires a separate immutable `LiveExecutionAuthorization` matching a
complete `ValidatedExecutionIntent` or explicit cancellation/replacement
request. Authorization issuance and administrative approval happen outside this
package. Missing, mismatched, future-dated, or expired authorization fails before
the broker interface is called.

Translation validates finite positive Decimal quantities and prices, symbol,
side, order type, time-in-force, required and prohibited price fields, and
timezone-aware timestamps. Requests are never automatically resized, repriced,
or converted. Replacement and cancellation are separate explicit operations.

The order state machine permits only documented transitions among `NEW`,
`SUBMITTED`, `ACKNOWLEDGED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, and
`REJECTED`. Fill reconciliation orders messages deterministically, deduplicates
exact fill IDs, rejects overfills, and records partial and full fills as immutable
events. Synchronization compares local orders, positions, and cash with broker
snapshots; it returns sorted differences and never overwrites local state.

`WebullAdapter` contains no HTTP client, endpoint, credentials, signing logic, or
MCP access. It delegates solely to an injected `Broker` transport, allowing the
entire boundary to be mocked. The existing `WebullMCPClient` read-only allowlist
is unchanged. Therefore this milestone provides architecture and deterministic
coordination but does not enable networked Webull trading by itself.

Execution JSON uses sorted keys, compact separators, exact Decimal strings, and
ISO-8601 timestamps. Text output is stable and explicitly states that broker
actions require separate live authorization.

## Deterministic market-data and event infrastructure

`app.market_data` is a lower-level, transport-abstract market-event subsystem.
It collects, validates, appends, records, and replays informational events. It
has no real network transport and imports no live execution, AI, indicator,
strategy, risk, compliance, analytics, Monte Carlo, or stress-testing package.
It cannot generate a decision, signal, order, or broker mutation.

Every immutable event carries a nonnegative sequence, timezone-aware source
timestamp, optional validated symbol, nonempty source, supported event type, and
matching typed payload. Supported events cover quotes, trade prints, full order
books, order-book deltas, market status, halts, resumes, symbol metadata,
corporate actions, session changes, heartbeats, and clock synchronization.
Prices are finite positive Decimals, sizes are finite and nonnegative, and
quotes and books require bid not greater than ask.

The event log is append-only. Accepted sequence numbers must strictly increase,
timestamps may not move backward, and duplicate source/sequence pairs are
rejected. Events are never sorted or rewritten after acceptance. Canonical
schema-versioned JSON retains exact Decimal strings and ISO-8601 timestamps;
schema-version-1 logs and legacy version-1 documents without an explicit version
field can be replayed.

Replay filters are fixed when an immutable replay state is created. Symbol and
inclusive timestamp filters preserve recorded order. Original timing reports
the exact recorded inter-event delay, accelerated timing divides that delay by
an explicit positive Decimal factor, and fixed-step timing reports an explicit
positive microsecond interval. Replay never sleeps, modifies timestamps, or
changes events. Pause, resume, sequence/timestamp seek, and cursor advancement
return new immutable states.

Sessions are never inferred from clock time. Pre-market, regular, after-hours,
closed, holiday, and halted states exist only through recorded session-change
events. Corporate actions explicitly represent splits, reverse splits,
dividends, symbol changes, mergers, and delistings with validated required data.

Clock synchronization reports exchange/local timestamps, measured latency, and
clock skew as integer microseconds without adjusting any event. Heartbeat
freshness uses an explicit caller-supplied current timestamp and maximum age.
Reports use sorted-key canonical JSON and stable deterministic text formatting.

## Webull production transport boundary

`app.webull` is the only package that knows about Webull authentication, HTTPS,
stream connections, endpoint paths, retries, rate limits, and broker response
serialization. It implements the live-execution `Broker` protocol while leaving
execution unaware of HTTP, JSON, authentication, MQTT, gRPC, and Webull-specific
responses. It imports no research, AI, indicator, strategy, risk, compliance,
analytics, Monte Carlo, or stress-testing component.

Immutable configuration requires HTTPS and WSS endpoints, an account identifier,
explicit timeout, bounded retry/reconnect policies, heartbeat limits, and
logging settings. Configuration is instance-scoped; there is no global mutable
transport state. OAuth authorization-code login and refresh are implemented
through injectable token and credential-store protocols. Access and refresh
tokens have explicit expirations, refresh automatically when required, and are
never returned to structured logs. For Webull signature-authenticated Trading
API deployments, the HTTP client accepts an injected signing-header provider;
the app secret remains client-side and is never logged or transmitted as a
header.

The production HTTPS backend uses the Python standard library with configurable
timeouts. GET query parameters and JSON request bodies are serialized in sorted,
deterministic order. Only classified transient timeout, network, HTTP 5xx, and
rate-limit failures are retried. Authentication failures, validation failures,
malformed requests, and broker rejections are never retried. Backoff is bounded
and deterministic; `Retry-After` takes precedence when supplied. The rate
limiter queues by invoking an injected sleeper once and never busy-waits.

Webull market data uses MQTT over WebSocket and order events use Webull's event
streaming facilities. `OfficialSdkStreamBackend` isolates an official Webull SDK
client behind the stream protocol. Parsed messages enter the immutable
market-data event log, where duplicate source/sequence messages are ignored and
out-of-order sequences fail closed. Reconnects are bounded, resubscribe to the
same sorted channel tuple, and update immutable health state.

Connection health records connection, authentication, stream state, measured
latency, reconnect count, and last successful heartbeat. Structured transport
logs redact passwords, tokens, authorization data, app/client secrets, and full
account identifiers. Tests inject HTTP, token, clock, sleeper, and stream fakes;
they never contact Webull or require an account.

## Production dependency and authorization boundary

Dependencies flow downward through neutral contracts:

```text
Research
   |
   v
Authorization --> Execution --> Broker Protocol <-- Webull Transport
                              ^
                              |
                    interchangeable brokers
```

`app.broker_protocol` exclusively owns the `Broker` protocol, sides, order
types, time-in-force, order statuses, order requests, broker orders, fills,
positions, cash, and account DTOs. These frozen, slotted models validate finite
Decimal values and timezone-aware timestamps. The package imports no execution,
Webull, authorization, research, risk, compliance, analytics, or market-data
code. Temporary names exported from `app.live_execution` are identity aliases to
the same classes and are not duplicate definitions.

`app.authorization` owns neutral `ExecutionIntent`, `RiskApprovalEvidence`,
`ComplianceApprovalEvidence`, and `LiveExecutionAuthorization` contracts. A
risk approval and compliance approval are evidence for their respective scopes;
neither is broker permission. Issuance requires both to be approved, active,
unrevoked, unsuperseded, and bound to the same exact intent ID and canonical
SHA-256 digest. Account, normalized symbol, side, quantity, order type, limit
price, stop price, and time-in-force must match exactly. Authorization expiration
cannot exceed either evidence expiration.

A live authorization is a separate, narrow permission containing the evidence
IDs and digests plus the complete broker-relevant intent identity. A
`ValidatedExecutionIntent` rejects an unrelated authorization at construction.
Immediately before submit, cancel, or replace dispatch, execution independently
checks the authorization registry, exact intent binding, active interval,
revocation state, evidence state, and consumption state.

Single-use authority is consumed immediately before broker dispatch. This is
intentional: if the broker call fails or its outcome is ambiguous, the authority
remains consumed and cannot be replayed. A new explicit authorization is needed
for another attempt. This favors duplicate-order prevention over automatic
retry. Cancellation and replacement require their own exactly bound and
registered authorization.

Authorization state is stored in a schema-versioned SQLite registry. Issuance,
consumption, revocation, evidence revocation, evidence supersession, and their
timezone-aware timestamps survive process restart. Consumption uses an
immediate database transaction plus a conditional update, so threads and
processes sharing the registry cannot both consume the same single-use identity.
SQLite WAL and FULL synchronous durability are enabled. Production instances
must provide a stable registry path; the no-argument constructor creates an
isolated durable file for test and compatibility use.

Raw Webull transport objects expose no `submit_order`, `cancel_order`, or
`replace_order` method. Mutations are available only through capability-checked
dispatch methods. The execution-owned `WebullAdapter` creates and binds the
opaque capability; possession of a connected transport alone is insufficient
to mutate broker state. Read-only account, cash, position, order, and fill
queries remain directly available.

### Durable live-mutation recovery

Every execution mutation can be recorded in the schema-versioned SQLite
`DurableExecutionJournal`. Its fixed state machine is:

```text
PREPARED -> AUTHORIZED -> DISPATCHING -> ACKNOWLEDGED
                              |
                              v
                         UNRESOLVED
```

`PREPARED` is committed before authorization consumption. `AUTHORIZED` records
successful durable consumption. `DISPATCHING` is committed before calling the
broker, and `ACKNOWLEDGED` records the broker order identity. The operation and
client-order identity have a durable uniqueness constraint.

At startup, reconciliation queries broker orders by the existing broker
abstraction. A consumed `PREPARED` or `AUTHORIZED` mutation that was definitely
not dispatched is dispatched once using its original immutable request. A
`DISPATCHING` mutation is acknowledged only when the broker reports the exact
client-order identity. If it cannot be found, it becomes `UNRESOLVED` and is
never automatically replayed; explicit operational resolution is required.
Repeated reconciliation of acknowledged or unresolved records is idempotent.

## Operational controls

`app.configuration` loads immutable TEST, PAPER, SANDBOX, or LIVE configuration.
LIVE rejects missing credentials, insecure endpoints, temporary database paths,
or absent explicit `LIVE_TRADING_ENABLED=true`. Credentials are accessed through
an injectable provider. Webull OpenAPI requests use the documented canonical
HMAC-SHA1 signature, UTC timestamp, nonce, signed headers, compact request body,
and optional access token; signed `/openapi/` endpoints never use the generic
Bearer fallback.

Operational submit and replace gates add symbol lists, limit-order-only
controlled rollout, notional, daily notional, position/order count, frequency,
quantity, regular-hours, market freshness, reconciliation freshness, and
unresolved-mutation limits. The durable emergency stop starts enabled and blocks
submit and replace while permitting cancellation, read-only queries, and
reconciliation.

The market-event SQLite store atomically appends canonical payloads with SHA-256
digests, ordered replay, restart cursors, idempotent identical duplicates, and
fail-closed conflicting duplicates or corruption. Operational modules provide
recursive secret redaction, canonical JSON logging, bounded-label metrics,
fail-closed readiness, periodic reconciliation orchestration, SQLite online
backup verification, an opt-in integration boundary, and soak-report models.
Deployment and incident procedures are under `docs/`.

Authorization JSON schema version 2 contains the full evidence binding and uses
canonical sorted-key serialization with exact Decimal strings. The former
five-field authorization had no risk/compliance evidence and therefore cannot be
safely migrated; deserialization rejects it explicitly rather than upgrading it
with fabricated approval.

`app.webull` imports only the neutral broker protocol for broker operations. It
has no dependency on live execution, authorization, risk, or compliance and does
not validate research evidence. Conversely, execution never imports Webull.
Market infrastructure remains an independent transport-fed event subsystem.
