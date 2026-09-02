# Atlas multi-strategy opportunity discovery

Phase 2A.1 adds a pure, bounded, research-only discovery subsystem under
`app/opportunity_discovery`. It broadens what Atlas can observe without
changing what Atlas can trade. The existing Warrior setup and execution path
remain the production champion and do not import this package.

## Safety boundary

Discovery accepts immutable decision-time facts and returns immutable research
detections. Its public contracts contain no broker, account, position, order,
risk, or execution-authority object. Research states deliberately exclude
`ENTRY_READY`, and registration never grants execution authority.

The subsystem is not wired into the desktop or market runtime in Phase 2A.1.
Future integration should publish `NormalizedOpportunityObserved` messages to
the Trade Intelligence research worker. It should persist separate append-only
strategy-membership records rather than rewriting existing Phase 1 experience
payloads. This preserves old datasets and keeps discovery off the production
authorization path.

## Point-in-time input

`DiscoveryContext` contains at most 64 strictly ordered completed bars. Every
bar must belong to the context symbol and have a completion timestamp no later
than the decision cutoff. Derived impulse, pullback, HOD, premarket, opening
range, consolidation, and reference-level facts use only that bounded prefix.
Future outcomes are not accepted as detector inputs.

Capabilities are explicit. A caller cannot supply VWAP or prior close unless
the corresponding authoritative capability is set. Missing required features
produce `UNAVAILABLE`; they are never silently replaced with zero or inferred
from future data.

## Taxonomy and availability

Taxonomy version `ATLAS_MOMENTUM_TAXONOMY_V1` defines 30 hypotheses. Detector
rules use version `ATLAS_DISCOVERY_RULES_V1`. All definitions carry a family,
description, required and optional features, availability, and an invariant
`research_only=True` marker.

| Availability | Count | Strategies |
| --- | ---: | --- |
| ACTIVE | 23 | MICRO_PULLBACK, FIRST_PULLBACK, HIGHER_LOW_CONTINUATION, SHALLOW_PULLBACK_CONTINUATION, DEEP_PULLBACK_RECLAIM, VOLUME_CONTRACTION_PULLBACK, MOMENTUM_REACCELERATION, HIGH_OF_DAY_BREAKOUT, FLAT_TOP_BREAKOUT, CONSOLIDATION_BREAKOUT, ASCENDING_BASE_BREAKOUT, RANGE_COMPRESSION_BREAKOUT, BREAKOUT_RETEST_CONTINUATION, OPENING_RANGE_BREAKOUT, PREMARKET_HIGH_BREAKOUT, PREMARKET_CONSOLIDATION_BREAKOUT, OPENING_DRIVE_CONTINUATION, FAILED_BREAKOUT_RECLAIM, HOD_RECLAIM, GAP_AND_GO_CONTINUATION, POST_GAP_RECLAIM, DIP_AND_RIP, MOMENTUM_SQUEEZE_EXPANSION |
| UNAVAILABLE_FEATURE | 4 | PRIOR_RESISTANCE_BREAKOUT, VWAP_RECLAIM, VWAP_PULLBACK_HOLD, HALT_RESUMPTION_CONTINUATION |
| INSUFFICIENT_CONTEXT | 2 | SECOND_PULLBACK_CONTINUATION, RED_TO_GREEN_MOMENTUM |
| FUTURE_RESEARCH | 1 | PARABOLIC_CONTINUATION |

Unavailable capabilities are authoritative prior-day/reference levels,
point-in-time VWAP, authoritative halt/resume facts, lifecycle pullback ordinal,
and a guaranteed authoritative prior close. `PARABOLIC_CONTINUATION` remains a
high-risk future hypothesis until its definition receives separate review.

`POST_GAP_RECLAIM` adapts the existing pure Warrior research geometry instead
of duplicating it. It remains non-executable.

## Identity and overlap

A detector episode is keyed by strategy, symbol, session date/session, and its
structural setup anchor. A normalized opportunity is keyed independently of
strategy by symbol, session date/session, and a bounded structural time window.
Consequently several hypotheses can classify one move without creating several
experiences. Quote, spread, scanner-rank, and ordinary price updates do not
alter structural identity; a new structural window does.

Each normalized opportunity retains a primary hypothesis and the complete,
sorted multi-label membership. Phase 2A can consume individual Boolean strategy
features plus a deterministic combination feature to evaluate single strategies
and overlaps.

## Outcome semantics

Discovery supplies a technical reference and structural stop when the observed
geometry supports them. Such a pair is a hypothetical technical R plan, never
an execution or fill. Opportunities without a valid structural stop remain
observable with `complete_r_plan=False`; R-multiple labels must then remain
unavailable or insufficient-plan while semantically valid price-forward labels
may still be completed by Trade Intelligence.

## Runtime integration constraints

A future observer should evaluate completed-bar updates incrementally, then
enqueue immutable observations to Trade Intelligence away from the market
callback. It must retain bounded per-symbol state and perform no SQLite reads,
report generation, model fitting, analog search, broker action, or policy
mutation on the market path. Phase 2A evidence gates and immutable temporal
TRAIN/VALIDATION/HOLDOUT generations remain unchanged.

## Extensibility

Detectors are registered objects. A new detector supplies one versioned
`StrategyDefinition` and one pure evaluator; it does not require modification
of an execution strategy, position manager, risk engine, or order path.
`ACTIVE` means only that Atlas can observe the hypothesis.

## Position-first continuity

Position identity and strategy identity are separate. The research projection
requires an opaque `AuthoritativePositionReference` supplied from the existing
PAPER/broker ownership path. It never reconstructs holdings, accepts fills, or
owns execution state. `position_id`, original opportunity, entry strategy,
entry version, entry time/price, initial stop, and initial risk remain immutable
while current memberships and append-only transition observations evolve.

The existing authoritative path is:

1. `PaperOrderBook` and gateway events own accepted orders and fills.
2. `AutonomousPaperExecutionBridge` correlates a Warrior lifecycle with the
   active symbol and reconciles that identity from the order book.
3. `PositionProjection` folds ordered PAPER fill/mark events into immutable
   desktop position snapshots.
4. `PortfolioProjection` aggregates those position and working-order snapshots;
   risk and exposure remain owned by the existing risk/portfolio components.

For Warrior-managed PAPER positions, `open_paper_symbols` feeds the sidecar's
`retained_symbols`, which is installed as the live scanner coordinator's
retained-channel source. The coordinator unions those symbols with scanner
subscriptions. Thus a managed Warrior position does not lose its market channel
solely because rank or scanner qualification changes.

The general production scheduler does not currently construct that retained
set directly from every authoritative portfolio position and working order.
Future discovery integration must union those authoritative sources before it
can claim the position-first invariant for positions outside the managed Warrior
lifecycle. Phase 2A.1 does not alter production scheduling.

### Focus priority

The future research observer has an explicit, deterministic priority contract:

1. open positions;
2. working orders or execution confirmation;
3. triggered opportunities;
4. forming opportunities;
5. general scanner discovery.

One highest-priority subject is retained per symbol. Scanner rank is not part of
position identity and cannot displace an open-position subject.

### Strategy and thesis transitions

Current memberships may be empty or may contain several strategies. Comparing
two immutable observations produces append-only `STRATEGY_JOINED`,
`STRATEGY_STRENGTHENED`, `STRATEGY_WEAKENED`, `STRATEGY_LEFT`, and
`STRATEGY_INVALIDATED` records. Each record retains position/original
opportunity correlation, observed opportunity, decision cutoff, and detector
versions. No required transition graph is hard-coded.

The research-only thesis states are `THESIS_INTACT`,
`THESIS_STRENGTHENING`, `THESIS_WEAKENING`, `THESIS_TRANSITIONING`, and
`THESIS_INVALIDATED`. Invalidation changes only the projection; it cannot close
a position or change quantity, risk, stop, or an order.

The 15-minute discovery window bounds opportunity normalization, not position
lifetime. One position may correlate append-only observations from many window
identities while preserving its original opportunity and immutable entry
strategy.

### Add-on and position learning compatibility

A later continuation can be represented as an `AddOnResearchCandidate` under
the existing position. The record may carry observed authoritative quantity,
risk, stop, and realized/unrealized R values, but explicitly has
`execution_authorized=False`. It cannot create a second position or order.

Learning features separate entry strategy from current memberships and the
observed join sequence. A future label join can therefore compare entry
intelligence with position-lifecycle intelligence—transition count, 1R/2R/3R,
MFE/MAE, stop-first, holding duration, and final R—without leaking any future
outcome into the transition itself.
