# Atlas Trade Intelligence Memory V1

This subsystem is an autonomous, observational research sidecar. It has no
broker, order, account, authorization, strategy-configuration, or execution
dependency. Runtime integration is intentionally not enabled in Phase 1.

## Ownership and storage

The default future runtime root is `data/atlas_learning/`, already excluded by
the repository-wide `data/` ignore rule. One versioned SQLite database should
own experiences, outcomes, temporary active bars, work accounting, imports,
and machine-generated research metadata. Logical subareas `models/`, `evaluation/`, and
`reports/` may be added only when artifacts actually exist; no JSON-per-event
storage is used.

## Identity/lifecycle contract

`OpportunityKey` is `(strategy, symbol, session date, session, episode ID)`.
The producer creates an episode at a meaningful scanner qualification or
Warrior setup transition and retains it across ordinary quote/trade ticks.
It terminates it only at an authoritative session/reset/terminal boundary or
when a distinct setup anchor forms. A concurrent setup requires a different
episode ID. Market-event IDs and changing prices are deliberately absent from
experience identity. A duplicate identity with different snapshot content is
rejected rather than silently rewriting decision history.

Meaningless repeated `NO_SETUP` evaluations must not be submitted. The pure
Warrior adapter accepts an explicit episode ID so lifecycle ownership remains
visible and testable rather than inferred from noisy ticks.

## Point-in-time and outcome contracts

The immutable experience contains only facts whose source timestamp is at or
before `decision_timestamp`. Every derived feature names its latest source
timestamp. Construction rejects future timestamps. Unavailable data is `None`,
never zero. Completed-bar feature extraction rejects bars not completed by the
cutoff. VWAP-dependent or anchor-dependent fields remain unavailable unless an
authoritative input is supplied.

Outcomes are separate immutable rows at 1, 2, 5, 10, 15, and 30 minutes.
They contain future return, MFE/MAE and, only when an authoritative trigger and
structural stop exist, hypothetical 1R/2R/3R and stop path data. Technical,
hypothetical-plan, and actual-trade fields remain distinct. Same-minute OHLC
ambiguity is conservative stop-first. Gaps are labeled insufficient rather
than interpolated.

For non-entered authoritative plans, the longest complete horizon yields:

- `PROFITABLE_MISSED_OPPORTUNITY`: at least 2R and stop was not first.
- `PROTECTED_REJECTION`: stop was first and 1R was not reached first.
- `DANGEROUS_FALSE_POSITIVE`: 1R was reached, then stop, without reaching 2R.
- `NEUTRAL_REJECTION`: complete path reaches neither the profitable nor
  protected/dangerous definition.
- `INSUFFICIENT_OUTCOME_DATA`: missing complete data or authoritative risk plan.

## Autonomous research and analogs

Automatic observation, labeling, classification, reporting, analog retrieval,
and future champion/challenger datasets require no operator trade teaching or
manual opportunity labels.

The same versioned database reserves non-executable `research_models` and
`model_evaluations` ledgers with feature lineage, training/evidence cutoffs,
partition identity, and the gated CHALLENGER → HOLDOUT → SHADOW → PAPER →
ELIGIBLE lifecycle. Phase 1 never writes policy settings or promotes a model.

Analogs use exact, explainable decision-time buckets for price, change, RVOL,
float, spread, setup, session, catalyst, pullback depth, HOD distance, and setup
state. Candidate membership never reads outcomes and requires a strictly earlier
decision timestamp. Outcome statistics are shown only at a configurable minimum
sample size.

The initial frozen V1 partition view assigns dates before 2026-07-01 to TRAIN,
2026-07 to VALIDATION, and dates from 2026-08-01 onward to HOLDOUT. Durable
research generations make later studies explicit. Each immutable generation
freezes its policy version, train/validation/holdout date ranges, evidence
cutoff, feature version, experience schema, and optional future model/policy
lineage. Assignments are append-only and session-date based.

A generation cannot include evidence later than its creation date. A successor
cannot reuse any predecessor holdout for training or validation until an
append-only completion record freezes the predecessor evaluation digest. When
reuse becomes legal, the successor must reserve a strictly later untouched
holdout. Database triggers reject update/delete attempts against generation
definitions and assignments, preserving exact reproducibility.

## Isolation, pressure, and recovery

The producer performs immutable serialization and bounded `put_nowait` only.
SQLite, outcome calculations, history reads, and reports execute outside the
market consumer. Full queues reject only that research work item, increment an
observable pressure episode, and accept again after capacity returns. No
permanent disable is triggered by transient saturation.

Accounting distinguishes accepted, checkpointed, started, completed,
duplicate-suppressed, rejected, failed, and outstanding work. Orderly shutdown
stops admission and drains accepted FIFO work. Checkpointed payloads survive a
restart and replay idempotently; incompatible store schema versions fail closed.
Future bars are retained only while experiences need horizons, bounding durable
and in-memory state to active opportunities rather than completed history.

Historical imports accept decoded rows from an explicitly supplied external
snapshot copy only. Provenance includes source path, source schema, import
version, and source record identity. The importer has no authoritative-store
discovery and never mutates its source.
