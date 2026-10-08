# CI baseline failure review

The 60 failures existed on `fix/research-backlog-footprint-circuit` before
Quick Scalper PR #18. This repair branch starts at `a30563bc` and keeps that PR separate.

## Original failure inventory

| Group | Failures | Cause and resolution |
| --- | ---: | --- |
| Historical artifact builder | 11 | Tests required an ignored, external corpus report. Use explicitly labeled synthetic schema fixtures; retain a separate external-report integration check. |
| Historical capture | 1 | The ignored Webull capture and manifest are absent from fresh checkouts. Retain an explicit external-data integration check; add deterministic loader roundtrip and tamper detection. |
| Decision intelligence runtime | 9 | The default artifact is unavailable in CI. Opt in to a temporary artifact built by the real builder for contract tests; explicit missing/corrupt-artifact tests keep their failure checks. |
| Desktop historical treatment | 3 | Tests expected synchronous authorization. Await the publication and assert durable PAPER orders, assignment links, restart continuity, and protection orders. |
| Experiment worker persistence | 1 | Diagnostics were read before the asynchronous callback finished. Await completion before checking durable assignment and diagnostics. |
| Forward capture control | 1 | The enabled intelligence test required the absent artifact. Supply the same explicit test-only artifact. |
| Event replay | 8 | Derived account notifications polluted the event timeline with wall-clock timestamps and generated IDs. Duplicate fills also debited cash twice. Exclude the derived snapshot from the timeline and deduplicate fills and delivery sequences. |
| Desktop runtime/startup/performance | 10 | Test doubles lacked current lifecycle fields and writer metrics, protection tests used the observational lane, and scanner tests assumed the old startup event order. Update fixtures and test the authoritative protection lane. |
| GUI | 4 | Expectations predated the visible but disabled order panel, current layout proportions, and local-time heartbeat formatting. Verify the current UI contract. |
| Strategy/scanner compatibility | 11 | Invalid composite events, old detector hashes and scanner ordering/warmup assumptions, inconsistent spread fixtures, insufficient reward in pursuit fixtures, low-RVOL discovery assumptions, and immediate sync/async comparisons. Align fixtures with current contracts and retain execution safety assertions. |
| PAPER restart projection | 1 | A direct composition test omitted authoritative order reconciliation and indexed a position after it was removed. Restore durable inventory explicitly and assert realized P&L through the account projection. |
| **Total** | **60** | |

## Production repairs

- PAPER account snapshots are derived state, not independent timeline facts.
- A fill ID changes cash and realized P&L once, including after authoritative
  reconciliation; per-source delivery sequences prevent duplicate event folding.
- Intelligence work remains active through its publication callback, so idle
  waits cannot race an execution re-drive. A blocked-callback regression proves this.
- Desktop shutdown stops the Warrior execution worker before closing its durable
  PAPER commands/store.
- Invalidated pursuit stops before price/reward evaluation, and an insufficient
  remaining-reward refusal has an explicit diagnostic category. Entry, spread,
  freshness, position, and risk limits remain enforced.

## Detector contract review

The golden-output changes were compared against commit `9162ecf`, where the hashes
were introduced. Differences are confined to structural provenance, opportunity
and setup anchors, and seven `NOT_DETECTED` to `TRIGGER_ARMED` state/reason pairs.
Commit `f66cc6f` added armed reference-level facts without changing the `DETECTED`
condition; `bdadd17` added session-root provenance and identity continuity. The
updated hashes describe that current contract. All 23 active detectors remain
covered by representative detected fixtures and adapter parity assertions.

## External-data boundary

The synthetic report is test evidence only. Its metadata is labeled
`SYNTHETIC_TEST_FIXTURE`; the V1 builder requires fixed corpus-count constants even
for schema fixtures. Those constants do not claim that synthetic rows reproduce
historical research. No synthetic artifact is installed at a production data path.
The two external report/capture checks skip with an explicit reason when their
inputs are absent and execute normally when the original files are present.
This repair does not establish historical strategy performance.

## Validation

Local full-suite validation: **6,051 passed, 12 skipped, zero failures** in
157.72 seconds under Python 3.13.15. Application compilation and patch whitespace
checks also pass. The 12 skips include 10 existing optional checks and the two
explicit external-data checks. CI status is recorded in the repair PR. No merge or Windows
branch switch is performed by this investigation. All execution exercised here is
PAPER; live trading configuration and safety thresholds are unchanged.
