# Warrior library entry ownership and review preparation

## Reproduced failure

The November 18 running check contained ten library entry preparations with bare
SHA-256 lifecycle identifiers, zero Warrior submitted orders and no resolved
native review. The raw identifiers match `ExecutionCandidate.as_warrior_setup`:
it projected the discovery execution hash directly into the Warrior signal.

The native review boundary, PAPER controller, admission reservation, acknowledged
entry adopter and strategy GUI all identify engines by a namespaced lifecycle.
`PreparedEntry.from_placement` therefore raised before review submission when it
received a bare library hash. `WarriorForwardCaptureService._open_paper` treated
that exception as potentially ambiguous dispatch and retained the symbol in
`AI_REVIEW_RECONCILIATION_REQUIRED`. No native receipt existed to release it.

History shows the two contracts were introduced separately: the research adapter
in September, the owned Grok/controller boundary in October, and the explicit
full-library PAPER selection in `4f3f18f` in November. Earlier tests exercised
library selection and Grok review separately, with a synthetic reviewer at the
boundary. They did not prove an actual library projection could pass that seam.

## Repair

Only new research-to-Warrior projections wrap the original discovery hash in
`WARRIOR_MOMENTUM_V1|<symbol>|TAXONOMY|<hash>`. Research opportunity identities,
family names, overlap deduplication and original hashes remain available. The
native signal, ownership registry, review, controller and protective orders now
use one engine-owned lifecycle. No controller ownership check is relaxed and no
persisted signal, ledger row or old order identifier is rewritten.

A local type/value preparation failure is explicitly `NOT_SENT` before submit is
called. The runtime reports `AI_REVIEW_NOT_SENT` and its detailed refusal, rather
than inventing dispatch uncertainty. Exceptions after the submission boundary and
unrecognized reviewer outcomes still retain ownership and require reconciliation;
there is no unreviewed buy fallback or automatic retry of uncertain orders.

`engine-readiness.json` and the read-only PAPER status now include
`warrior_execution_status`: the real Warrior quote-source counters, native offer
outcomes, pending candidate identities and whether their original authorization
leases are current. This is a passive projection; reading it does not poll away
receipts, refresh leases, request a quote, call Grok or place an order.

## Validation and limits

Regression coverage uses the actual discovery adapter projection for every one of
the 23 implemented families, a deterministic model transport, the native producer
and review worker, the real controlled PAPER service and its SQLite controller,
fill events, owner-lane receipt adoption and quantity-matched protective stops.
The entry-path test fails before the identity repair and passes after it. A second
regression reproduces the false ambiguous-dispatch state for a local preparation
error. A full observation/quote-refresh test exercises actual detector selection
through native review and controller submission. Existing bare-hash inventory
recovery retains its old identity and protection. Stale/revoked authorization,
unknown dispatch, duplicate orders and all existing ownership gates remain tested.

These are offline contract and execution-flow checks, not evidence of profitable
trades or a restored external Webull connection. Quote age limits, provider times,
strategy parameters, stop/target logic, sizing and Grok review policy are unchanged.
The old running check still showed stale quotes and reconnect failures; this
repair removes an independently reproducible entry blocker but cannot make stale
provider data fresh. New readiness fields make subsequent waits attributable.

## Install and inspect

Close Atlas normally. Extract `tools/repair-warrior-library-entry.ps1` from the
exact published revision and run with `-Repo`, `-Revision`, `-PaperDirectory`.
It checks the existing continuous PAPER spec, runs offline regressions, creates a
backup branch and fast-forwards only. It does not launch Atlas, start crypto,
change credentials, replace ledgers or alter the operating specification.

After a manual PAPER launch, run `tools/Show-Warrior-Entry-Status.ps1` with the
same `-Repo` and `-PaperDirectory`. It saves an uploadable local status JSON and
selects it in Explorer. A closed recording is explicitly marked historical.
