# Trial-bound closed PAPER review

`capture-paper-day-review.ps1` locates the native equity simulator ledger using
the installed configuration, original repository working directory and the
explicit day-trial flags. The configuration digest must match the recorded native
manifest. An exact `-Ledger` path can be supplied if configuration later changes;
this is a read-only export, not an execution grant. It tests in a detached checkout
and does not update the installed branch, launch Atlas/Grok, or edit `.env`.

The exporter requires the exact trial/session manifest and a finalized capture.
It makes consistent read-only SQLite backups of the paper ledger, native attempt
ledger and Grok role ledger into a fresh private folder. Bounded copies of final
capture metadata and all observer-run summaries, source/preflight bindings,
input-health reports and proposals accompany these backups. Secrets/environment
files and the large raw journal are not copied. Retain the original journal for
later causal replay and hash-chain verification. Snapshot hashes are recorded.
The resulting ZIP is a private review package, not a public benchmark artifact.

Native selection requires the exact `strategy_trial_id` on filled BUY requests,
then joins their durable lifecycle/symbol/campaign identities to the existing
canonical fill reconciler. Partial exits and recorded commissions are reconciled
by that reader. Older trial outcomes are excluded. Lifecycles with mixed-trial BUY
inventory are excluded explicitly; missing, open, unbalanced and ambiguous
lifecycles are not counted as closed break-even trades. A trial spanning a
nonactive campaign refuses automatic performance attribution. The active-campaign
5,000-order/32-MB reader bounds still apply. Reconciliation failure is reported
without manufacturing zero performance, while the private snapshots remain
available for review. Reported PnL includes recorded commissions only and retains
unverified simulator provenance. It is not a live trading result.

The report includes actual NEWS statuses, citation counts and elapsed times from
the saved role ledger; a missing SCOUT handoff is not described as successful
news/scouting. Grok proposals have no separate filled-position ledger and produce
no measured win rate. Closed-capture completeness is the writer's reported state,
not an independent verification of the full journal chain by this export.
