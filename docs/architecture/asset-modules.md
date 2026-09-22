# ATLAS asset modules and crypto paper supervisor

This build separates market navigation from activation. The four global tabs apply
across the sidebar pages. Selecting a tab never starts a data subscription.
The dropdown permits one or two active markets; reducing the budget requires
stopping another market first. Ordinary deactivation refuses known open positions
or working orders. Application exit preserves paper positions; it does not flatten
or continue managing them while closed. Emergency equity controls retain their
existing behavior.

## Delivered behavior

- Equity retains its existing execution, learning and risk engine. Crypto is removed
  from its Mission Control layout. The global status footer is equity-only.
- Crypto discovery, optional configured intelligence/catalyst workers and a separate
  paper supervisor start with the Crypto module. They are stopped cooperatively and
  can restart without restarting the GUI. Disabled modules do not poll markets.
- Views for crypto positions, fills/activity and AI decisions never reuse equity rows.
  Hidden crypto views skip refresh work. The crypto scanner remains research-only;
  it cannot place orders. Paper proposals cross an explicit, separate simulator API.
- Futures and options have contract-specific analysis workspaces and working P/L
  scenario calculators. They remain ANALYSIS ONLY: no connected quotes, simulated
  derivative fills, autonomous trading, margin engine, exercise or assignment.
  Their typed registry integration points start no workers.
- One or two active markets is a workload ceiling, not a RAM/CPU guarantee. Existing
  equity event processing continues when equity is active but its tab is hidden.

## AI authority and setup

Set `GEMINI_API_KEY` and `ATLAS_SUPERVISOR_MODEL` locally (environment or untracked
.env), restart Atlas, start Crypto, then enable AI paper proposals on its Mission
Control page. No model is chosen implicitly, no key is bundled, and the checkbox is
OFF after every application restart. It discloses sending crypto observations and
simulated account state to Gemini. Research-only decisions are inputs, not execution
permission. No external AI call is made by tests or installation.

`GeminiProposalProvider` uses the documented REST generateContent JSON response
mode. `CryptoSupervisor` accepts an injected provider with `propose(context)` so a
future provider can be evaluated without modifying execution. Calls are limited to
one per minute, two proposals per response, 120 requests per process lifetime, a
15-second HTTP timeout and a 64 KiB response. Costs vary by chosen model. Protection
has its own worker, independent of model latency. Quotes are rechecked after calls;
revoked proposals are discarded. HOLD, accepted and rejected model proposals appear
in Decisions (last 100 shown, last 1,000 stored).

The AI cannot edit source code, change limits, invoke a shell, operate equity orders,
or access a broker execution API. This is a bounded crypto paper supervisor, not a
self-rewriting financial adviser or a proven profitable strategy. Automated code or
parameter promotion is not implemented. Equity AI supervision is not wired in this
build. Any future parameter learning needs walk-forward evaluation after realistic
costs, versioned candidates, holdout evidence and rollback before promotion.

## Crypto simulation constraints

Separate `crypto-paper.sqlite3` beside the configured execution database:
$10,000 starting cash; spot USD pairs; max two positions; $250 notional per entry;
$25 estimated stop risk per entry; cumulative realized loss cutoff of $100 blocks
new buys. There is no leverage. Entries require bid/ask <=90 seconds old, a spread
<=1%, sufficient cash, a stop below entry and a target providing >=2R plus estimated
costs. Simulated fees and slippage are each 0.1% per side. Stops can gap beyond the
estimated risk budget: the $25 limit is not a guaranteed maximum realized loss.

Fills use current snapshots, not an exchange order book. Queue priority, partial
fills, latency variation, exchange minimum sizes, variable fees and delistings are
not modeled. The paper limits do not establish profitability or live readiness.
The close-paper-positions button pauses proposals and attempts exits only with
fresh quotes. Missing/stale protection quotes are shown explicitly; the engine
never manufactures a fill. An unavailable provider means no new AI orders.
Configured crypto research permissions/data subscriptions still matter. This change
does not buy subscriptions or activate catalyst providers disabled in configuration.

Crypto Replay and strategy editing remain unavailable. This is not full equity
feature parity. Existing research outcomes and the new paper journals remain separate;
there is no automatic training or promotion from simulated P/L.

## Future adapters

Keep execution and data identities keyed by asset, venue, currency and canonical
instrument, not ticker alone. A futures adapter must provide contract multiplier,
tick size/value, expiry/roll, session calendar, margin and liquidation behavior.
An options adapter must provide underlying, expiry, strike, call/put, multiplier,
exercise/assignment, corporate-action handling and Greeks/volatility snapshots.
Neither may reuse spot cash sizing or the equity close-of-day policy. Each adapter
must implement start/stop/active/exposure, its own instrument validation and paper
fill/risk tests before appearing available. The module limit applies to either.

## Validation

Targeted market lifecycle, crypto runtime, simulator, supervisor and desktop GUI
suites exercise separation, stop/restart, stale quotes, duplicate IDs, fees, invalid
size/risk, provider revocation and protection during blocked AI calls. No live broker
or AI provider is contacted. Windows installer reruns the same focused suites.

An unrelated existing test,
`test_production_desktop_historical_treatment_full_lifecycle_survives_restart`,
fails at `intelligence._journal.execute` because `_journal` is None. This failure was
reproduced independently on unchanged base commit b964ce5 in an isolated worktree;
it is not counted as passing or silently repaired by this build.

Final local run: **283 passed, 1 explicitly deselected** in the focused suites.
The excluded baseline failure is described above. The Gemini REST adapter has not
been exercised against a paid account, and Windows installation must still run its
local validation. No exchange execution adapter was added.


## Entry economics and market workspaces (September 22 follow-up)

The IMCC screenshots show a partial fill at 4.0799, with 136 shares and a stop at
3.915; the remaining buy expired. The later 4.25 mark and +23.14 unrealized result
are consistent with that holding. These pictures do not prove that the strategy
can identify a future peak or that the entry was optimal.

The code previously moved all targets upwards when raising the entry to an ask.
The patch establishes the volatility-adjusted plan before execution repricing,
preserves its targets, sizes against actual entry-to-stop risk, and rejects new
entries or replacement requests if remaining reward is insufficient. The gate
also covers the legacy replacement path without depth sizes. Planned exits cannot
silently raise the preserved targets. Existing positions are not retroactively
replanned; only new entry and replacement decisions use the new gate.

Default paper policy: remaining first-target reward must be at least 0.5 times
risk; final-target reward at least 1.5 times risk. Both reward and risk reserve one
quoted spread for execution friction. EntryConfig holds the two thresholds. These
are engineering guardrails, not calibrated profitability estimates. Targets remain
R-based plans, not forecasts of achievable upside. Fees, gaps and adverse selection
are not fully modeled. The previous absolute/percentage displacement caps remain.
No early-entry predictor, AI model improvement, or proven trading edge is claimed.
Rejected entries record INSUFFICIENT_REMAINING_REWARD and cannot return an
executable signal. This policy needs forward paper evaluation before promotion.

Crypto Mission Control now groups positions, a ranked spot scanner and selected-pair
intelligence. It shows bid/ask, spread, volume acceleration, time-of-week relative
volume, volatility, research events and quote age. Stale/missing position marks
make equity/unrealized unavailable. Bid marks exclude exit costs. Only visible
views refresh, using existing in-memory research snapshots. No extra data worker
or AI provider is started by opening a page. Catalyst and depth not supplied by the
snapshot are explicitly unavailable rather than invented.

Futures columns cover exact contract, expiry, bid/ask, volume, open interest,
tick size/value, margin and quote time. The scenario calculator uses tick-based
P/L, whole contracts, direction and entered fees, and validates tick alignment.
Options columns cover underlying, expiry, strike, right, bid/ask, volume/open
interest, IV, Greeks, multiplier and time. The long-call/put calculator models
expiry payoff only, with an explicit multiplier. No pre-expiry theoretical price,
short-option exposure or assignment model is implied. Scenario inputs are not
broker data and never submit orders. Orders/positions stay empty without adapters.

### Primary research sources

- Coinbase, order books: https://www.coinbase.com/learn/advanced-trading/what-is-an-order-book
- CME, contract and tick P/L: https://www.cmegroup.com/education/courses/introduction-to-futures/calculating-futures-contract-profit-or-loss
- OIC, volatility and Greeks: https://www.optionseducation.org/advancedconcepts/volatility-the-greeks
- Webull data/permissions: https://developer.webull.com/apis/docs/market-data-api/overview/

Webull documents separate OpenAPI subscriptions for OPRA options and CME-group
futures data, independent of app subscriptions. Those entitlements have not been
verified on the user's machine. Full futures/options testing remains blocked on
implementing and validating their market-data and paper-execution adapters;
these screens do not claim that work is done.

### Validation limits

Four test_forward_capture.py failures were reproduced on untouched commit 61f977b:
test_enabled_entry_experiment_control_uses_normal_paper_path,
test_authoritative_exit_submission_and_partial_fill_do_not_close,
test_profit_defense_tracks_peak_and_tightens_after_confirmed_giveback, and
test_profit_defense_runner_exit_is_limited_and_preserves_milestones.
The modified full file has the same four failures; the other 46 tests pass.
They are not hidden by claiming that the complete suite passes. The installer
runs the separate new entry-economics regressions plus existing strategy, adaptive
exit, execution bridge and market UI suites. The previously documented historical
journal failure is still excluded from the composition suite.

Installer-equivalent validation: 385 passed, 1 documented pre-existing test deselected.
No authenticated derivative API or external AI call was run.
