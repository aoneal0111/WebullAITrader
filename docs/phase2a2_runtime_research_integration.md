# Phase 2A.2 Runtime Research Integration

Phase 2A.2 attaches the multi-strategy discovery engine to the existing Trade
Intelligence sidecar. It is an observation path only. Scanner and Warrior keep
their existing production responsibilities, and discovery exports no order,
broker, account, risk, stop, sizing, or authorization capability.

## Runtime boundary

The existing authoritative market event callback remains the single ingress.
Warrior's completed-minute-bar publication supplies the preferred point-in-time
context. The Trade Intelligence observer also maintains a bounded fallback
minute accumulator. A symbol/cutoff identity suppresses duplicate evaluation
when both sources observe the same completed bar.

The callback constructs an immutable envelope containing no more than 64
completed bars and calls `put_nowait` on the existing bounded Trade
Intelligence queue. The worker performs detector evaluation, normalization,
transition projection, and SQLite persistence. Quote-only changes do not run
technical discovery. No SQLite read or write, model fitting, analog search, or
report generation occurs on market ingress.

## Focus and ownership

Observation priority is open position, working order, triggered opportunity,
forming opportunity, then general scanner discovery. The retained-symbol union
combines the existing managed Warrior PAPER channel with authoritative desktop
position and working-order projections. GUI selection is not ownership.

Position correlation is an observational reference to the execution-owned
position. A detector can never create a position. Position identity survives
scanner exit, strategy evolution, and the 15-minute opportunity-window rollover.
Entry strategy attribution is recorded only when authoritative PAPER lifecycle
evidence permits it; otherwise it remains unknown. Strategy memberships,
transitions, thesis states, and add-on candidates are research-only append-only
facts. Thesis invalidation and add-on candidacy have no execution effect.

## Persistence and compatibility

Store schema version 3 adds immutable tables for normalized opportunity
observations, strategy memberships, position strategy transitions, position
correlations, thesis observations, and add-on candidates. Existing Phase 1 experience and outcome rows retain their
meaning and payloads. Migrations create new tables and update only the schema
metadata version.

All accepted runtime work is checkpointed through the existing work ledger.
Normal shutdown stops acceptance, drains the bounded queue, and exposes final
per-strategy coverage plus accepted/completed/failed/rejected/outstanding
telemetry. Abrupt process termination can still lose work that had not reached
the durable ledger.

## Future learning

The append-only records support entry intelligence and position intelligence:
individual strategies, multi-strategy combinations, and post-entry transition
sequences can later be joined to deterministic outcomes. Phase 2A evidence
gates and temporal partition rules are unchanged. No challenger is trained,
promoted, or connected to execution by this integration.
