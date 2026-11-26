# Atlas test-suite and deadweight audit — phase 1 (2026-11-19)

## Evidence and scope

This is a **non-destructive inventory**, not a claim that Atlas's trading
system is complete. The evidence is the recursive Git tree at
`bcd5e6ee951ec9cd8dea79bd389b03f23e6b66f6`, the project test
configuration and the current CI workflow.

| Tracked category | Count | Tracked bytes |
| --- | ---: | ---: |
| All Git blobs | 2,992 | 19,776,253 |
| `app/` blobs | 1,429 | 9,604,181 |
| `tests/` blobs | 1,217 | 5,907,569 |
| Python files in `tests/` | 1,216 | Not separately calculated |
| `docs/` blobs | 220 | 1,297,739 |

The difference between 1,216 Python test files and 9,000+ pytest test cases
is normal: parametrized functions produce multiple collected cases.

The Git tree contained **no identical-blob duplicate `test_*.py` files**.
This is only an exact-content check: it does **not** exclude logically
redundant tests, outdated assertions or duplicated parametrized cases.

## Findings

1. CI deliberately executes the complete suite. Its second full-test stage
   excludes only `tests/dynamic_momentum_discovery`, which runs separately
   to isolate a historically observed Qt/thread shutdown crash.
2. Project-local pytest uses `tests` as its collection root. Do not
   silently drop or skip existing tests for a faster green result.
3. The `.gitignore` contained repeated rules for Python caches, pytest
   state, and build artifacts. The phase-1 change collapses only exact
   repeated entries.
4. The README's historical foundation section says automated execution
   does not exist. That text describes the project's original version, not
   current PAPER runtime capabilities; revise historical documentation in
   a separate docs review rather than deleting useful history.
5. The two unusually large active strategy files
   (`forward_runtime.py`, `autonomous_paper.py`) should be decomposed
   only after call-site, persistence and rollback mapping. File size alone
   cannot demonstrate dead code.

## Trading-critical preservation set

Do not prune validation of:

- Actual PAPER order admission, fill price/quantity, and cancellation.
- Protection continuity, target/stop sibling reconciliation, partial fills.
- Durable order recovery, restart, stale position projection, idempotence.
- Broker-neutral and live-trading-disabled boundaries.
- Fresh quote provenance, session gating, entry authorization and kill switches.
- Backward compatibility of persisted order and operator evidence.

## Deletion eligibility

A test or component is eligible for removal only when ALL of the following
hold:

1. The feature's production/research entry points and all import paths are
   inventoried; reflective imports and configuration-dependent paths count.
2. Historical durable artifacts and replay compatibility do not depend on
   its contracts or serialization.
3. Replacement tests cover its **behavioral invariant**, not just an import
   or a mock success response.
4. A dedicated commit removes the feature/tests together, preserves
   actionable history, and passes focused, Critical, and Full suites.
5. No regression in PAPER position protection, cancellation, recovery,
   scanner timing or GUI startup is observed.

## Test workflow

- `Fast` for frequent gateway/order-book code changes.
- `Critical` for trading logic, protective exits, and integration.
- `Full` before branch integration. CI continues its full suite and
  intentionally separate research-worker checks.

All three are implemented by `tools/run-atlas-tests.ps1` without starting
the trading application. The operator's installed checkout should remain
unchanged until the integration branch is verified.

## Priority

**First** complete and end-to-end verify owned-position protection,
profit-retention and entry execution; **then** perform destructive
cleanup. Small documentation and test-runner simplifications can happen
in parallel. There is no justified target test count and no evidence
yet that deleting thousands of cases would improve trading behavior.

## Phase-1 result

- Zero application modules removed.
- Zero test files deleted.
- Repeated ignore rules consolidated.
- Explicit local test tiers added.
- Historical and forensic artifacts preserved.
- Exact-duplicate test-file inspection completed; behavioral duplicate
  analysis remains open.
