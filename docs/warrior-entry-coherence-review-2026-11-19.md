# Warrior entry coherence review — 2026-11-19

Scope: current `research/high-win-rate-comparison` source only. PAPER, Grok OFF. No Windows runtime, broker orders, ledgers, or installed configuration changed by this review. A code default is not proof of the effective Windows configuration.

## Goal and invariant

Participate early in justified momentum; permit a genuine catalyst-supported second leg; decline stale, structurally invalid, uneconomic, or risk-unapproved entries. Do not manufacture a buy by adding independent exceptions or allowing one gate to bypass another.

Existing pipeline: scanner discovery -> completed-bar setup / taxonomy -> native technical structure -> quote freshness and same-generation confirmation -> adaptive executable price and economics -> trigger confirmation -> account/risk/ownership -> PAPER authorization and bracket protection. The October 24 early-entry study is OFFLINE; it is not an authorization route.

## Current control inventory

| Concern | Current source | Default / behavior | Consistency finding |
| --- | --- | --- | --- |
| Discovery | `configuration.DiscoveryConfig` | $1-$100, >=5% preferred gain, 2x preferred RVOL, $250k dollar-volume baseline | Adaptive PAPER context does not treat every preferred ranking metric as a hard stop. |
| Scoring | `ScoreWeights`, `EntryConfig` | 0-100; preference >=55; technical setup score >=55 | Score is an adaptive priority; triggered structure can be retained below preference. Do not create a second momentum score. |
| Structure | `setups.py`, taxonomy adapter | Completed-bar triggers, pullbacks, HOD/flat-top/acceleration, structural invalidation | Seeing a high-scoring scanner row is not an entry. Earlier intrabar hypotheses remain research-only. |
| Executable quote | `execution_quote.py`, `paper_stream_quote.py` | Strict Webull component timestamps, same generation; Warrior 2s execution limit | Never retimestamp provider data to create an entry. |
| Price displacement | `runtime.execution_displacement_percent`, `forward_runtime._execution_entry_signal` | Baseline 1.5% from current structural trigger, adaptive maximum 3%, absolute outer limit $0.50 | Existing absolute and percent constraints stay authoritative even with strong news/volume. |
| Initial-move extension | `entry_extension.py`; `forward_runtime._execution_entry_signal` | Provisional >3% from first observed price is exhausted absent **all**: catalyst TRUE, RVOL>=2, positive 1m/5m price velocity and acceleration, setup score >=75 | This overlaps trigger displacement but measures a different origin. Do not interpret the 3% as a global daily-change cap. Same-day anchor check added. |
| Remaining economics | `entry_economics.remaining_reward_ok` | Min first target 0.5R, last target 1.5R after one quoted spread | 0.5R first-target floor is **not** equivalent to management's first planned 1R partial. Actual executable entry can deteriorate reward. |
| Adaptive order replacement | `AdaptiveEntryConfig`, `_consider_adaptive_entry_replacement` | Up to 2 replacements, 5s repricing interval, 1.5% and $0.05 configured pursuit caps | Different scope from a new entry. Must not be used to evade original risk or ownership constraints. |
| Trade management | `TradeManagementConfig` | Planned 50% at 1R, 25% at 2R, 25% runner; executable-BID harvest and profit defense separate | Reconciliation and stop/target continuity take precedence over entry-frequency tuning. |
| Session/account authority | `session_risk.py`, risk bridge, controller | PAPER-only; runtime can have expanded allowed sessions | Check effective operator configuration and ownership; do not infer from historical docs. |
| Grok | STOP_GROK operation marker | Native-only configured startup; historical controller manifest may say Grok | Verify actual runtime binding after install. |

## Specific divergences / gaps

1. `docs/warrior_momentum_v1.md` describes older conservative defaults ($1-$20, >=10%, 5x RVOL, 60 score, etc.) rather than the current Balanced values. Treat its concepts as historical, not the installed parameters.
2. The new extended-breakout continuation exception reads `CatalystStatus.TRUE`, **not a timestamped fresh headline**. The five independent conditions reduce but do not remove the risk of stale catalyst attribution. Do not claim verification of *news freshness* from this predicate.
3. `_setup_lifecycles` is symbol-keyed; its `first_observed_at` is a symbol observation anchor, not a new setup episode. The anti-chase guard now declines to apply the old anchor when its UTC calendar date differs from evaluation. Same-day distinct episodes still require separate lifecycle-aware validation.
4. Entry-age and price extension are not interchangeable. Do not add a universal 'minutes since earnings' or 'price already up x%' rejection. A fresh pullback/reclaim needs its own setup and risk evaluation.
5. The provisional 3%/2x/75/positive-velocity thresholds are **not** measured profitable settings. They need shadow replay by setup family, session, provider-time coverage, net R, MAE/MFE, slippage, and holdout; compare to unchanged baseline and the two losing Nov 19 cases.
6. The PDD and SVRE local captures prove timing observations, but **not** that either alternate entry would have been profitable or that this new rule would have prevented losses under real-time available evidence.

## Release checklist

- Focused Warrior entry, quote, controlled PAPER service, entry economics, opportunity, ownership and protection tests pass.
- Explicitly test early valid entries, stale/future quotes, high-momentum continuation and fading move, missing velocity, renewed trigger, prior-day cache anchor and normal pullback route.
- Run local same-data baseline-versus-candidate replay **after costs**. Track false negatives (missed sustained movers) as well as losses avoided. The anti-chase policy must not secretly change scalper or existing SELL/cancel behavior.
- Verify exact Windows code/config hash, stop marker, PAPER account and existing protective orders before any controlled trial.
- Never promote a policy solely because one synthetic test succeeds or because trade count increases.

**Decision:** keep the new anti-chase policy provisional and uninstalled pending controlled tests and historical performance comparison. Prioritize consolidation and evidence over more independent entry heuristics.

## Follow-up repair: working BUY pursuit uses same anti-chase policy

The optional `_consider_adaptive_entry_replacement` previously reassessed current
reward, quote, liquidity, depth, risk, and bounded replacement budget without
rechecking the initial-breakout move since first observation. A working BUY
could therefore pursue a later price through a separate route after a fresh
initial entry was refused. Both initial executable price evaluation and
working BUY pursuit now invoke the same `_initial_breakout_exhausted` method,
which delegates to `entry_extension.initial_breakout_move_exhausted`.
A valid catalyst-supported acceleration can pass either route, and existing
account/quote/reward/sizing/replacement constraints still apply. No SELL or
protective-order path was altered.

Regression explicitly asserts that the working BUY path reaches the shared
predicate; a stale quote or absent order adapter must not make the test pass
for the wrong reason. Test success is not proof of profitable entries.

## Profit-target alignment held for historical comparison

The configured first protective target is nominally 1R from the structural
entry plan, but the remaining-reward guard requires only 0.5R after spread
and repricing. PDD and SVRE forensic fills show under-1R initial-target
geometry. Raising the minimum to 1R can reduce trades and still not account
for actual partial fills, so it is **not silently promoted** here. Compare
0.5R (baseline), 1.0R and net-of-cost first-target alternatives on matched
executions and held-out dates, then check first-target fills, giveback,
win rate, net expectancy, and unprotected exposure before changing execution.


## After-close protection review — 2026-11-19

Forensic PAPER orders contained repeated STOP cancel/replacement events for
SVRE and PDD. Order timestamps alone cannot prove the position was unprotected
at every instant; the paper simulator and correlated target reservation state
must be considered.

Source review found two **optional stop-tightening** branches and one
**positive-quantity stop reconciliation** branch that canceled the existing
STOP before a replacement STOP was durably accepted when an atomic amendment
port was unavailable. These branches now fail-closed: preserve the existing
STOP, mark management incomplete, and report reconciliation failure rather
than claiming a successful change. Target-first correlated bracket creation
still has a cancel-first transition and is **not declared solved by this patch**.

Retaining an old stop is not the same as complete coverage: after quantity
growth, it can underprotect inventory; after quantity shrinkage it can
over-cover and must not be treated as risk-approved. A missing stop or a broken
target/stop correlation still requires safe repair. A STOP in the local PAPER
simulator is also not a continuously active broker stop while Atlas is offline.

Validation requirement: exercise failed amendment, failed replacement,
partial fill, quantity mismatch, contingent target transition, outage/restart
and duplicate STOP states with the real PAPER gateway and durable order book.
Observe coverage and market-event ordering, not just submission boolean.
Keep new entries paused until a confirmed quantity-matched stop is working.
No live orders, ledger migration, or global cleanup is authorized.


## Follow-up protection pass: recoveries and target submission failure

Additional source audit found a cancel-first recovery branch in
`AutonomousPaperExecutionBridge.reconcile_protection`: when a working STOP
did not match authoritative quantity and no atomic amendment port existed,
the existing STOP could be canceled before replacement submission. Recovery
now retains the old STOP, reports management incomplete and awaits an explicit
safe amendment. This does not make mismatched inventory safe.

The correlated target-install branch still cancels a standalone STOP
before submitting the target and its contingent replacement STOP. Its current
recovery logic attempts an immediate STOP re-install on target failure.
That failure path now also marks the lifecycle **incomplete**, even when
the replacement STOP is accepted, since the profit bracket was not installed.
Regression tests model the target placement failure using the actual in-memory
PAPER gateway/order book, exercise replacement STOP restoration and assert the
incomplete state; independent recovery tests assert no cancel-first attempt.

**Outstanding structural problem:** no guarantee of zero unprotected
milliseconds during standalone STOP -> target+contingent STOP conversion.
Eliminating that requires a gateway-supported atomic reservation/replace
transaction or equivalent verified state machine, not a speculative
cancel-and-submit retry. Do not approve production installation on these
changes alone. Check paused PAPER runtime's existing protective orders and
run controlled deterministic failure-injection and replay tests first.


## Required gateway atomic target/STOP transaction — design contract

Source inspection on November 19 confirms that the gateway currently
provides `amend_protective_stop(order_id, remaining, price, correlated_target_order_id)`
under its mutation lock, but this operation only amends an **existing**
STOP and validates an **existing** target. It does not create a target and
link it to the STOP in one atomic durable state transition.

The present bridge method
`_place_target_with_correlated_protection` cancels the standalone STOP
first, then submits a target, then submits a new correlated STOP.
An additional characterization regression records that **zero working
STOP orders exist at the instant target placement begins**. A successful
final bracket does not erase that intermediate protection gap.

The correct follow-up is a gateway-level, PAPER-only
`stage_correlated_target_with_existing_stop` transaction, not a
blind bridge-level reordering:
- Take gateway's exclusive order/matching mutation lock; verify position,
  account/lifecycle ownership, authoritative quantity, stop identity/price,
  target quantity and duplicate target state.
- Construct a target reservation and a STOP correlated to that target
  as one validated state change. Journal all affected durable order
  transitions together before publishing any event.
- If validation or durability fails, leave the previously working STOP
  unchanged. Never claim success on partial persistence, and never
  convert into an uncovered position.
- On success, retain STOP trigger authority for the full remaining
  position, ensure the target reservation does not double-reserve shares,
  and use the existing trigger-side OCO and target-fill reconciliation.
- Exercise immediate downside quote, concurrent fill, partial target fill,
  restart with WAL replay, persistence failure and duplicate recovery
  deterministically before enabling the new operation.

Until this transaction exists and is verified, do not assert
zero-gap target protection, and do not install these changes based solely
on passing unit tests. Existing PAPER protection checks and orders remain
authoritative; no LIVE support is authorized.


## Atomic PAPER bracket implementation — research branch only

The gateway now exposes `stage_correlated_target_with_stop`. Under the
existing gateway mutation lock it validates the working protective STOP,
same-symbol/same-lifecycle target, filled inventory, order quantity,
duplicate client IDs, and the absence of conflicting sibling SELL orders.
It builds a target (LIMIT or the existing immediate MARKET role) and
upgrades the **same STOP identity** to contingent OCO. The durable store's
existing multi-order `persist_batch(events, orders=(target, updated_stop))`
persists both orders and their three events in **one SQLite transaction**.
Only after successful persistence does the gateway mutate the in-memory
order book or emit events. If durability fails, the gateway becomes
fail-closed and the old in-memory STOP is preserved.

The desktop PAPER composition injects this gateway operation into Warrior's
`AutonomousPaperExecutionBridge`. In that path, staging a new target no
longer cancels an existing STOP. The legacy standalone bridge fallback
remains for isolated research harnesses that do not inject the new port;
those callers are not evidence of uninterrupted protection.

Added tests cover direct atomic staging, stable stop identity, rejected
quantity, duplicate target, desktop-equivalent bridge injection, synthetic
durable-write failure, restart with both orders restored, and a target fill
reducing the correlated STOP. CI's focused step now includes the durable
target and gateway suite before the unrelated full test collection.

This is a proposed PAPER-only repair, not an installed or validated
real-broker OCO feature. Do not represent the local matching guarantee
as protection that survives shutdown or loss of market data. Verify
multi-process durabilities, partial and simultaneous matching, full
recovery, and effective Windows configuration before promotion.
