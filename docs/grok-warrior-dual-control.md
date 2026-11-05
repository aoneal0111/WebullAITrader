# Explicit Warrior dual control in PAPER

`GROK_WARRIOR_V5` is a new eight-hour experimental profile, created only when the operator manually launches it. No model, GUI or order is started by the installer. Live trading remains disabled. This profile is not evidence of profitability and does not connect the daily RSI or NFI backtest candidates to execution.

## Decision authority

| Path | Strategic decision | Execution path |
| --- | --- | --- |
| Native Warrior proposals | Grok approves or abstains on native, already qualified plans. | Existing asynchronous native review, controller, order gateway, acknowledgement and protection. |
| Grok-directed Warrior | Once per parent-trial hour, Grok may choose BUY, EXIT or PASS from up to four fresh subscribed symbols plus authoritative Warrior holdings. A legacy named pattern is not required for this path. | Model prices/quantity become a separately attributed `GROK_DIRECTED` signal only after local validation; the canonical controller requotes and validates account/risk again. Acknowledged entries install normal Warrior management. |
| Quick Scalper | Native Scalper remains in control. | No model review callback is attached. Shared account allocation, symbol ownership and native protective exits still apply. |

Grok's own lane does not scan the entire internet or all tickers. It uses the existing subscribed market-data stream and at most 32 completed minute bars per symbol. No news or missing market data is invented. The native-review lane still inherits Warrior's candidate rules; the directed lane receives market context before that setup evaluator. Data availability and subscription eligibility still bound what it sees.

A BUY must select a symbol present in its sealed input, then pass a newly received local context, current tradability/halt/session checks and a new executable quote. It cannot change account mode, extend a trial, authorize itself, remove a stop, buy a Scalper-owned instrument or choose live execution. Entry limit displacement is bounded; prices must be finite, stop < entry < target, whole shares <=10, position <=$100 and modeled initial price risk <=$1. The shared parent trial retains 50 attempts / $5,000 total entry notional. Gaps can exceed modeled stop risk.

EXIT is allowed only for the exact still-owned Warrior lifecycle and current authoritative inventory. It cannot sell a Scalper or Crypto position. It uses the existing immediate-exit bracket handling. Native protective and profit-defense behavior can still exit before a later model opinion; model unavailability never removes protection.

## Timing, budget and isolation

- The model runs on background workers. It does not execute on the native quote/protection lane. The existing owner lane alone applies completed decisions and adopts orders.
- The original Warrior allocation is twenty paid attempts total: at most twelve native candidate reviews and eight director calls (one per trial hour). Transport failures/in-flight requests count. Both lanes share a transactional durable ledger, including across restarts. Other observer use of that trial's Warrior allocation consumes the same total cap.
- Director evidence explicitly carries `mode: PAPER` and `asset: EQUITY`. A transport-boundary reservation hook runs after local context validation, concurrency and role-cooldown checks. Locally refused requests do not consume this controller's durable allocation; a refused budget reservation cannot reach transport. `MODEL_REQUEST_NOT_SENT` distinguishes these cases from transport attempts. Previously recorded reservations remain unchanged; no historic usage is refunded and no order is retried automatically.
- This is **not continuous unlimited model coverage**. Twelve native reviews can be consumed early. Remaining requests per lane are shown in `warrior-control-status.json`; no automatic paid retries or budget resets occur.
- A director answer older than ten seconds from its input context is discarded. Dispatch additionally requires current eligibility/context and two-second executable quote freshness. A slow answer is never made fresh by rewriting provider timestamps.
- The native Scalper retains its existing five-second quote policy in this new profile. The general Grok execution pilot still defaults to two seconds. Both layers of the controller enforce the selected engine policy; an arbitrary model field cannot select it.
- `STOP_GROK` pauses Warrior's new model-controlled decisions without expiring the native Scalper trial. `PAUSE_ENTRIES`, trial expiry, capture failure and account/risk limits still stop new entries as designed. Existing protection continues independently.

## Operation and evidence

Install with `tools/install-paper-day-trial.ps1 -ExperimentProfile GROK_WARRIOR_V5 -RecordedSession <closed-session>`. The frozen replay is a prior Scalper compatibility check, not a simulation of Grok performance. Windows checks run before source installation. A backup branch is created; no tracked changes are overwritten and no `.env` file changes.

The replay recognizes the exact V3 Scalper blobs shipped at `0151b213114480e35765ece14133c842ee811be8`, including Windows CRLF checkouts. That version has identical entry-assessment arithmetic and snapshot schema; its absent profit-defense setting is restored as disabled in memory. V2 and V3 recordings cannot claim the newer defense setting. Unknown, missing or mixed-version source files still refuse installation. Original recording bytes, timestamps and journal hashes remain unchanged; this check does not replay runtime exits.

Use the newly printed `Start-Atlas-Paper-Trial.ps1`, then click START normally in the visible desktop. Warrior control is embedded. Do not start an old Grok observer alongside it. The dashboard notice names Grok Warrior control and native Scalper. `Show-Morning-Status.ps1` reports recording, engine authority and the control status. The eight hours begin on the first manual launch, not at installation.

Independent context refresh runs on the shared market stream before Warrior full-evaluation scheduling. Repeated updates coalesce for 250 ms; provider times remain unchanged. Holdings receive priority in the four-symbol model batch.

The raw native recorder remains active. The durable model budget retains input evidence, source times, replies, safe failure categories and latency. Direct entries carry `GROK_DIRECTED_PAPER` provenance, a `GROK_DIRECTED` family and a lifecycle containing the director-hour event key. Native-reviewed trades retain their actual family and can be linked through the review budget's candidate evidence. The standard private closed-day export now includes control status. Request records retain cumulative usage snapshots keyed by a provider-client instance ID; these are not per-request costs and must not be summed directly. Model usage is reported separately from realized P&L; actual billing/operating expense still needs reconciliation.

Do not treat a pending model request, an acknowledgement, a fill or an unclosed position as a winning trade. Neither this controller nor the >60% daily-swing research results establish a >60% Warrior win rate. The new cohorts require their own forward results.

## Explicit extended-session PAPER option

`install-paper-day-trial.ps1 -ExperimentProfile GROK_WARRIOR_V5 -ExtendedSessions -RecordedSession <closed-session>` prepares a new launcher carrying `--extended-sessions`. The first manual launch seals the three entry sessions in `experiment.json`. Existing trials and their budgets are not edited. Ordinary V5 launchers retain their original session policy.

This option configures Warrior, native Scalper and both Grok director checks for PREMARKET, REGULAR and AFTER_HOURS. The Scalper's entry calendar becomes a continuous 04:00–20:00 Eastern cycle on valid trading days, with no five-minute entry cutoff at the premarket/regular or regular/after-hours boundaries. Its existing maximum holding time is reserved before the final 20:00 boundary (normally the last entry is 19:55). Holidays/weekends, unsupported overnight sessions, stale quotes, halt/tradability checks, ownership, position/risk limits and protection remain in force. No synthetic closed-market fills are created.

The opted-in policy travels with new Scalper intents, persisted order metadata and replacements. Delayed/recovered orders retain their original deadline; the extension cannot revive expired orders or retrospectively authorize old positions. Unknown/non-boolean policy metadata expires entry authority. Exact prior V4/V5 source hashes, including CRLF variants, remain recognized by the frozen assessment replay; the replay does not simulate the new session behavior or exits.

Preparation starts no GUI, trial timer, model or trade. Launch on the intended test day. The eight-hour entry window still begins at the first manual launch, and the 20 Warrior request / 50 shared entry caps still apply. This is PAPER trading against incoming data, not unrestricted or live brokerage trading. The launcher, dashboard and status report display the selected sessions and remaining limits.

## Native account sizing in V6

`GROK_WARRIOR_ACCOUNT_V6` is a separate explicit PAPER experiment. It removes
the V5 trial's 10-share, $100-position, $1-initial-risk, 50-attempt and $5,000
cumulative-entry-notional overrides. The recorded `limits` fields are null for
these extra trial ceilings. The eight-hour expiry, recording requirement and
20 paid Warrior requests (12 native reviews / 8 director calls) remain.

Warrior and Scalper receive the normal configured native risk policy, rather
than the V5 clamped version. Grok chooses its directed order quantity using
supplied actual buying power, equity, exposure and native risk configuration.
Its response schema uses the configured native quantity maximum. Fresh quotes,
account buying power, sizing, symbol ownership, shared account capital/loss
budgets and native protective exits are still rechecked at dispatch. This is
not unlimited capital, permission to enlarge account limits, or live trading.
Actual normal limits may still cause a proposed order to be rejected.

Both native Warrior review and director context include the configured partial
exit fractions, native first/final reward thresholds and protective-management
settings. These are facts for independent review, not instructions to approve;
a first partial target is not the entire trade's payoff, and configured
fractions remain subject to whole-share rounding, native exits and fills.

Prepare a new launcher with `-ExperimentProfile GROK_WARRIOR_ACCOUNT_V6
-ExtendedSessions`. Existing trials, budgets, positions and recorded outcomes
are preserved. The launcher/dashboard/status name native account sizing, and
entry attempts remain durably recorded even though there is no extra trial
attempt ceiling. This change does not repair stale provider quote timestamps
or establish a profitable strategy. NFI crypto remains disconnected.
