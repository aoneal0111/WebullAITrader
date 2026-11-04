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
- The original Warrior allocation is twenty paid attempts total: at most twelve native candidate reviews and eight director calls (one per trial hour). Failed/in-flight requests count. Both lanes share a transactional durable ledger, including across restarts. Other observer use of that trial's Warrior allocation consumes the same total cap.
- This is **not continuous unlimited model coverage**. Twelve native reviews can be consumed early. Remaining requests per lane are shown in `warrior-control-status.json`; no automatic paid retries or budget resets occur.
- A director answer older than ten seconds from its input context is discarded. Dispatch additionally requires current eligibility/context and two-second executable quote freshness. A slow answer is never made fresh by rewriting provider timestamps.
- The native Scalper retains its existing five-second quote policy in this new profile. The general Grok execution pilot still defaults to two seconds. Both layers of the controller enforce the selected engine policy; an arbitrary model field cannot select it.
- `STOP_GROK` pauses Warrior's new model-controlled decisions without expiring the native Scalper trial. `PAUSE_ENTRIES`, trial expiry, capture failure and account/risk limits still stop new entries as designed. Existing protection continues independently.

## Operation and evidence

Install with `tools/install-paper-day-trial.ps1 -ExperimentProfile GROK_WARRIOR_V5 -RecordedSession <closed-session>`. The frozen replay is a prior Scalper compatibility check, not a simulation of Grok performance. Windows checks run before source installation. A backup branch is created; no tracked changes are overwritten and no `.env` file changes.

Use the newly printed `Start-Atlas-Paper-Trial.ps1`, then click START normally in the visible desktop. Warrior control is embedded. Do not start an old Grok observer alongside it. The dashboard notice names Grok Warrior control and native Scalper. `Show-Morning-Status.ps1` reports recording, engine authority and the control status. The eight hours begin on the first manual launch, not at installation.

Independent context refresh runs on the shared market stream before Warrior full-evaluation scheduling. Repeated updates coalesce for 250 ms; provider times remain unchanged. Holdings receive priority in the four-symbol model batch.

The raw native recorder remains active. The durable model budget retains input evidence, source times, replies, safe failure categories and latency. Direct entries carry `GROK_DIRECTED_PAPER` provenance, a `GROK_DIRECTED` family and a lifecycle containing the director-hour event key. Native-reviewed trades retain their actual family and can be linked through the review budget's candidate evidence. The standard private closed-day export now includes control status. Request records retain cumulative usage snapshots keyed by a provider-client instance ID; these are not per-request costs and must not be summed directly. Model usage is reported separately from realized P&L; actual billing/operating expense still needs reconciliation.

Do not treat a pending model request, an acknowledgement, a fill or an unclosed position as a winning trade. Neither this controller nor the >60% daily-swing research results establish a >60% Warrior win rate. The new cohorts require their own forward results.
