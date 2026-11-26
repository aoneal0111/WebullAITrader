# Native Scalper triggered-stop progress

A partially filled, already-triggered stop must continue executing. The bridge
correctly refuses replacement stops or profit targets while it is active, but
the Scalper repeatedly requested those replacements during reconciliation and
reported the refusal as SCALPER_EXIT_SUBMISSION_FAILED.

Before requesting protection, the Scalper now checks for one active SELL STOP
with the exact same lifecycle, QUICK_SCALPER ownership, stop_triggered=true, and
remaining quantity equal to the proven remaining position quantity. It retains
that order and records TRIGGERED_STOP_WORKING as exit progress instead of
requesting another stop or recreating the profit target.

Progress diagnostics are coalesced per position, stop order and remaining
quantity. Counts represent observed quantity states, not unique trades. They
appear under scalper_execution_status.exit_progress and as bounded
SCALPER_EXIT_IN_PROGRESS journal records. Historical failure counts are retained
in old recordings; this change does not rewrite prior performance or events.

Missing, insufficient, excessive, duplicate or foreign-owned stop coverage does
not take this shortcut. Existing bridge failure handling remains active. The
bridge's triggered-stop replacement guard is unchanged. Paper matching, fills,
stop prices, entry parameters and position sizing are unchanged.

Real-gateway regressions exercise a partial stop, repeated reconciliations, a
price rebound, further partial fills and a final fill. They verify preservation
of the same stop identity, no recreated target, no oversell, correct progress
classification, and complete position closure. Separate cases retain failure
reporting for wrong coverage and foreign ownership.

The installer requires Atlas to be closed and retains the current trial,
launchers, deadline, paid budget, ledger and environment file. It starts nothing
and does not restart crypto. It includes the preceding stream recovery repair.
