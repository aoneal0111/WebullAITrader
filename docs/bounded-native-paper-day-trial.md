# Bounded native PAPER day trial

An explicitly requested forward experiment may collect new evidence without passing a production profitability gate. It is not a release promotion or a claim of a winning strategy. The default release registry remains empty and the normal desktop launch retains qualified-only BUY admission.

## Scope

The manually launched trial enables the existing canonical native Warrior and Quick Scalper paths. It does not install all reviewed public repositories or route raw research detections into orders. Warrior family allowlist: FIRST_PULLBACK, HOD_RECLAIM, HIGH_OF_DAY_BREAKOUT, BREAKOUT_RETEST_CONTINUATION, OPENING_RANGE_BREAKOUT. Scalper allowlist: QUICK_SCALP_STREAM and QUICK_SCALP_MOMENTUM. A family is executable only if the native engine actually produces its canonical signal; an allowlist is not a new strategy adapter. The native exits are retained, and are not claimed to match every offline fixed-exit experiment.

## Native orders

The trial is PAPER-only, live trading off, Grok order review off, and optional paper controller off. Source and engine configuration fingerprints, engine owner and trial ID must match. Native risk sizing is clamped to 10 shares, $100 per position and $1 modeled initial stop risk. A dispatch boundary independently verifies the limit order, whole shares, stop geometry, risk metadata and caps before submission. At most 50 entry attempts and $5,000 total entry notional are durably reserved in SQLite before gateway dispatch. Failed or uncertain gateway attempts consume their reservation. Add-ons and non-entry dispatch reasons are refused. These are limits on modeled stop risk, not a guarantee against stop gaps.

Existing freshness, account, symbol authorization, ownership, campaign loss and gross-exposure gates still apply. The trial does not force a trade, reset an account, alter native signal rules, or remove session checks. Trial ID is preserved through durable order metadata. Startup quarantines inherited buys before trial fingerprints are bound. Existing position protection and SELL/cancellation paths pass through independently.

An eight-hour lease is created only when the operator launches the visible Atlas. Restarts reuse the same lease and consumed entry budget. Expiry, removal, a PAUSE_ENTRIES file, clock discontinuity or unhealthy raw recording stops new admission. Gateway validity is shortened to the earliest existing validity, lease expiry or 60 seconds; working buys after revocation may remain valid for at most 60 seconds. Protective exits remain available: keep Atlas open while positions need its normal simulator management.

## Capture and observation

The ordinary visible Atlas records subscribed native market events (including actual available book payloads), bars, eligibility, stream/setup assessments, confirmations, rejection diagnostics and PAPER order/fill events. Raw journal hard allowance for this trial: 4 GiB / 8 million records, up to 16 hours; 6 GiB free space required at preparation. Defaults outside trial remain 512 MiB / 1 million records. Queue loss, disk errors or capacity exhaustion are reported; they do not become complete evidence. Fresh-context capture retains its separate 10,000 record / 64 MiB cap. Raw recording continues if the smaller context sample fills. The normal local outcome collector is enabled in a separate local trial journal, with no model or broker authority.

The trial configuration is included in the recording identity and frozen native settings. A local session pointer identifies the exact recording; tools do not guess the newest folder. The dashboard displays the experimental entry status and limits. This is not whole-market history and cannot capture unavailable data, unobserved symbols, news or a depth feed that is not delivered.

A separate manual Grok process verifies installed source/configuration and recording/trial identity. It samples each engine at most hourly for eight hours, capped at 20 paid requests. Native execution never waits for it. It logs BUY/PASS/EXIT order intentions with suggested entry, stop, target and reason, input hash, timestamps, latency, token accounting and expiration status. Baseline result is withheld from the model. The same bounded client and low-reasoning research transport are used, with no tools or retries.

**Grok intentions are not submitted orders or simulated fills.** There is no Grok-owned filled-position ledger in this version. Therefore no Grok win rate can be calculated from these intentions. EXIT is an opinion, not native position management. Proposals older than 30 seconds when received are marked expired, never retroactively filled. A later counterfactual fill engine must use quotes available after response completion and its own inventory/cost/expiry rules.

## Operator sequence

Close Atlas normally. Run the pinned installer, which tests before fast-forward installation, creates a backup branch and generates local manual scripts. It never starts Atlas, makes API calls or edits `.env`. Run the printed `Start-Atlas-Paper-Trial.ps1` and click START normally. Run the printed `Start-Grok-Intentions.ps1` in a second shell. The launch profiles use process-only variables and restore them on exit.

Use `Pause-New-Entries.ps1` to stop admission while leaving protection running, and `Stop-Grok.ps1` to stop model observations. Close Atlas normally after the experiment to finalize its recording. Preserve the normal durable PAPER ledger, the trial attempt database, learning journal, raw recording and Grok private proposals. Do not publish private performance. Share small readiness/final summaries for review. No repo strategy is promoted by this trial.
