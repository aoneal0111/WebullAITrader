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
- Futures and options are visibly unavailable. The typed asset registry and lifecycle
  adapters provide their integration points. They start no workers.
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
