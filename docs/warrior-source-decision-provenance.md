# Warrior PAPER authorization source-decision identity

This change adds read-only provenance metadata. It does not add an entry
gate, change strategy thresholds, submit orders, or alter LIVE authorization.

Warrior `MomentumCandidate.decision_generation_id` already identifies the
canonical decision record using a bounded deterministic 24-character hex ID
derived from symbol, scanner observation timestamp, evaluation timestamp,
quote timestamp and structural episode. The `DECISION` capture event exposes
that ID as `decision_id`.

The opportunity assessment now carries the same ID as
`source_decision_id`, and the `AUTHORIZATION_EVALUATED` transition records
it when present and syntactically valid. Invalid or missing candidate IDs
become JSON null, rather than being guessed, expanded, or logged as arbitrary
strings. Both accepted and rejected authorization decisions are annotated.

This creates an explicit linkage between the captured candidate assessment
and authorization attempt in **new** PAPER captures. It is not a retroactive
proof for October's FSLY, JAGX, WFF and AEHL events, which lack that field.
The presence of an ID by itself also does not prove an order was filled or
that the decision remained valid at the market price used for submission.

Research consumers should join the `DECISION.decision_id` to the
`STATE_TRANSITION.source_decision_id` with campaign/configuration/symbol
provenance, check the decision timestamp is no later than authorization and
cross-check the actual order lifecycle. Missing, duplicated, mismatched and
post-authorization decision IDs are evidence gaps, **not** license to infer a
causal defect.

This is an observability-only addition and does not change normal D3
diagnostics defaults or introduce forensic file creation.

## Offline exact-ID verification

`capture_entry_audit.audit_entries` now reports each matched authorization's
`source_decision_id` and `source_decision_status`:

- `SOURCE_DECISION_ID_MATCHED`: exactly one same-campaign, same-config,
  pre-authorization captured DECISION shares that ID.
- `SOURCE_DECISION_ID_NOT_RECORDED`: legacy or unavailable field; not a
  defective trading authorization.
- `SOURCE_DECISION_ID_UNAVAILABLE`: field exists, but the bounded nearby
  capture has no eligible decision with that ID.
- `SOURCE_DECISION_ID_AMBIGUOUS`: multiple eligible records share that ID;
  do not guess which was used.

The entry/exit diagnosis repeats this evidence per entry order without
altering its historical P&L, mismatch or success/failure classifications.
All statuses are research observations, not automatic buy rejections.
