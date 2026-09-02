# Atlas Opportunity Learning Engine Phase 2A

Phase 2A is an offline, explainable challenger foundation over immutable Trade
Intelligence Memory. It learns, scores, analyzes, compares, reports, and proposes
research challengers. It is not imported by desktop composition and has no
broker, order, account, strategy-mutation, sizing, authorization, or veto API.

## Reused Phase 1/1B contracts

- `TradeOpportunityExperience` is the logical opportunity episode. Its stable
  identity excludes ticks and preserves environment, policy, strategy, model,
  feature, source-event, and imported-source lineage.
- `DecisionObservation` is append-only decision history. Each row has its own
  immutable cutoff, complete point-in-time snapshot, blockers, lifecycle stage,
  and factual Atlas decision.
- `HorizonOutcome` supplies deterministic 1/2/3R-before-stop, stop-first,
  MFE/MAE, timing, and availability labels at 1/2/5/10/15/30 minutes.
- `MissedOpportunityClassification` distinguishes profitable misses, protected
  rejections, dangerous false positives, neutral rejections, insufficient data,
  and non-applicable traded episodes.
- Phase 1 research generations freeze date/session partitions and reject
  mutation, future evidence, same-session splitting, premature prior-holdout
  reuse, and later holdout overlap.
- Phase 1 analog signatures already use explainable decision-time buckets and a
  strict earlier-than cutoff. Phase 2A generalizes this as a research challenger.
- Imported records retain source store, source schema, import version, and source
  record identity for deterministic deduplication.

The underlying experience schema is V1, durable store schema is V2, decision
features are `ATLAS_DECISION_FEATURES_V1`, temporal partition policy begins at
`ATLAS_TEMPORAL_SPLIT_V1`, and Phase 2A derives
`ATLAS_LEARNING_FEATURES_V1`/`ATLAS_PLAN_PATH_LABELS_V1` without rewriting source
rows.

## Eligibility and anti-lookahead

Every meaningful decision is a separate learning example. The vector is copied
only from that decision's snapshot and from deterministic values derivable at
its cutoff. Every source timestamp must be at or before the cutoff. A later
decision can create a later example but cannot mutate or enrich an earlier one.
Outcomes are labels only.

A classification example is eligible only when a COMPLETE horizon contains
authoritative plan-path fields for 1R, 2R, 3R, and stop. Incomplete,
session-boundary, gap, and planless outcomes are censored—not converted to
negative labels. Technical, hypothetical-execution, and actual PAPER expectancy
remain distinct; Phase 2A never invents an entry or execution price.

The V1 snapshot authoritatively contains price/change/spread/volume/RVOL/float,
tradability/halt/freshness, catalyst state/type, scanner score/rank/rules, setup
state/type/quality, trigger/stop/risk-share, blockers, and whatever completed-bar
features were captured before the cutoff. Missing fields remain `None` and are
paired with explicit missing indicators by fitted encoders. Features that were
not captured—such as full pre-decision bar history—cannot be backfilled.

## Immutable generation and holdout protocol

`LearningGeneration` composes the existing immutable `ResearchGeneration` with
label version, model-family version, hyperparameters, selection criteria,
evaluation criteria, and `ATLAS_RESEARCH_GATES_V1`. Models fit TRAIN only.
Calibration and selection receive VALIDATION only. APIs reject HOLDOUT rows for
fitting or calibration. HOLDOUT is evaluated once after selection and its digest
can freeze the generation through the Phase 1 completion contract.

The initial gates require 200 total examples, 120 train, 40 validation, 40
holdout, 30 positive and 30 negative labels, 10 dates, 20 symbols, 3 sessions,
20 examples per reported cohort/analog group, and ten training examples per
fitted feature. These thresholds are intentionally not lowered for the current
small live sample.

## Cohorts and challengers

Blockers are reported as initial, ever appeared, cleared, persistent, sole, or
multiple. Tied blockers suppress causal language. Setup, catalyst, spread,
RVOL/float, pullback depth, higher-low, volume contraction, momentum velocity,
and distance-from-HOD cohorts report sample/effective sample, 1/2/3R and
stop-first rates, MFE/MAE means and medians, deterministic R where definable,
Wilson uncertainty, and symbol/date/session concentration.

The challengers are deterministic historical analog, empirical grouped cohort,
L2-regularized logistic, and validation-only Platt-calibrated score models.
Outputs use only `RESEARCH_SKIP`, `RESEARCH_WATCH`, `RESEARCH_FAVORABLE`, or
`RESEARCH_HIGH_CONFIDENCE`, plus research confidence tiers. None resembles or
produces production `ENTRY_READY` authority.

Evaluation includes AUC/PR-AUC where meaningful, Brier score, log loss,
calibration error/buckets, deterministic-R win/loss/average/median/drawdown/
profit-factor/tail loss, concentration, and factual champion-versus-challenger
selection groups. The champion is reconstructed from stored decisions; Warrior
is not reimplemented.

## Data and artifact safety

Authoritative SQLite is never a training input. `create_external_snapshot`
hashes and stats every present main/WAL/SHM member, copies the complete family to
an external directory, then requires source stability and byte-identical copies.
`ImmutableSnapshotReader` rejects declared authoritative paths and uses SQLite
`mode=ro` plus `query_only=ON` only on the copy.

Model publication writes compact, immutable, hash-verified JSON model cards
under a caller-selected versioned research directory. It does not embed source
datasets, and runtime-generated model artifacts are not intended for Git.

The maximum Phase 2A result is
`CHALLENGER_ELIGIBLE_FOR_FRESH_SHADOW`. Phase 2B must collect new shadow evidence,
followed by a separate PAPER execution-validation phase. Promotion is never
automatic.
