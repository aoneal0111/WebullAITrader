# Atlas Core Streamlining

Baseline: `2d0920be8dfa349e0eb61efbc0224ff193a51c24`

This branch reduces Atlas conceptually without changing trading authority.

## Golden path

Only work required for the next authoritative market decision may execute in the synchronous market-event lane:

1. Webull event normalization
2. Authoritative market state
3. Scanner state / candidate state
4. Warrior adaptive participation
5. Canonical Warrior setup
6. Current execution safety
7. Risk and sizing
8. Order lifecycle / duplicate-order protection

Everything else is downstream.

## Runtime lanes

### Core / authoritative

Owns:
- typed market-data semantics
- authoritative accumulated volume
- scanner state
- canonical Warrior setup lifecycle
- quote/freshness/spread safety
- risk and sizing
- order state

Properties:
- deterministic ordering
- no lossy handoff
- no network or filesystem I/O added to event callbacks
- bounded state

### Projection

Owns:
- GUI/watchlist/timeline presentation
- non-authoritative focus and formatting
- display-only diagnostics

Properties:
- bounded
- coalescing where intermediate states are not semantically required
- must never authorize/reject an order
- must never block authoritative processing

### Research

Owns:
- Trade Intelligence research/taxonomy
- adaptive-entry research
- SEC/symbol intelligence
- footprint/capital-flow research
- crypto research/catalysts
- future social/counterfactual research

Properties:
- optional
- exception-contained
- bounded
- detachable without breaking Warrior execution

### Persistence / forensics

Owns:
- event/decision/outcome journals
- replay evidence
- diagnostic artifacts

Properties:
- asynchronous where safe
- bounded queues
- failure cannot alter trading authority

## Ownership rules

Each trading concept has exactly one production authority:

| Concept | Production owner |
| --- | --- |
| market event semantics | market_data / Webull normalization |
| accumulated current-day volume | scanner adapter authoritative volume state |
| scanner qualification | realtime scanner |
| adaptive participation | WarriorAdaptiveContext |
| setup lifecycle | canonical Warrior setup evidence |
| execution eligibility | Warrior forward runtime |
| quote/freshness/spread safety | Warrior execution boundary |
| risk/sizing | risk/sizing runtime |
| orders | broker/paper order lifecycle |
| taxonomy/research setups | advisory only |
| GUI state | projection only |

## Streamlining constraints

Refactors on this branch must not:

- change LIVE execution authority
- change setup geometry or thresholds unless separately justified
- restore legacy RVOL execution authority
- restore the legacy $2.5M execution veto
- make GUI/research/persistence execution-authoritative
- introduce unbounded queues or histories
- delete local forensic evidence merely to clean Git status

## Planned tranches

1. Repository hygiene and generated-artifact exclusion.
2. Composition split: core trading construction vs optional research/projection construction.
3. Reduce synchronous hot-path work and duplicate projection calculation.
4. Consolidate repeated lifecycle/state helpers around canonical owners.
5. Remove dead compatibility/experimental paths only after call-site and test proof.
6. Shrink oversized diagnostic/composition modules without changing behavior.
7. Local Windows validation against the exact branch checkpoint.
