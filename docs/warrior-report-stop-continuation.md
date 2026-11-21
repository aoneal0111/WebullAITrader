# Warrior report finalization during Stop

Warrior's observational report worker has a bounded five-second close wait.
A report may still be running at that deadline even after native execution has
stopped. The sidecar retains report/writer ownership and raises the typed
ReportWorkerShutdownPending condition so a later Stop can continue cleanup.

## Change

The broker driver's observer cleanup now continues that typed pending
condition within the same operator Stop, using up to three existing close
attempts. The GUI runtime remains STOPPING during continuation; entries and
market-data processing have stopped before this stage. It emits
WARRIOR_REPORT_FINALIZING while the retained report owner finishes.
No new report, model request, order or runtime is started by this continuation.

Every unrelated exception propagates immediately, including a native Grok
acknowledgement timeout. Persistent report-pending state also propagates after
three attempts. The sidecar owns report/writer resources until its normal close
completes. This does not detach ownership, claim a clean stop early or delete
research data. Completion may take longer than the old observer wait. Arbitrary
blocking SDK or research functions are not forcibly terminated.

The runtime-review capture requires an explicit artifact path or run ID. It
never picks the newest files, which can belong to the installer's tests. The
runtime installer no longer invokes an ambiguous automatic capture.

## Verification and deployment

Offline tests cover pending-report continuation through RuntimeService with a
clean final state, bounded persistent-pending failure and immediate propagation
of unrelated cleanup exceptions. The updater also runs shutdown provenance,
stream recovery, subscription, account retry and report equivalence tests.

The updater creates a backup branch and leaves Atlas stopped. Strategy settings,
Grok review policy, operating spec, ledgers and crypto workers are unchanged.
This is a report-shutdown fix, not a provider outage repair or proof of restored
trading performance. Production verification remains necessary.
