# Native Warrior and Scalper review branches

The native engines now accept explicit `paper_entry_reviewer` callbacks. Desktop
composition does not enable or supply them by default. Their synchronous PAPER
paths remain unchanged when no reviewer is supplied.

## Warrior

The reviewer receives the original signal, sized shares/risk, float provenance,
symbol authorization and evaluation timestamp, after the existing execution and
risk gates. A valid one-candidate `NativeReviewReceipt` creates a bounded pending
binding. It creates no `_paper` position, exports no executable signal, and does
not record authorization, order submission, or a shadow rejection.

The opportunity enters `REVIEW_PENDING` and retains its existing symbol claim.
Repeated eligible evaluations may call `paper_entry_review_refresher`; they do
not call the reviewer or synchronous submitter again. A reviewer exception or
malformed receipt retains an uncertain lifecycle claim. It can be recovered
only by a matching canonical acknowledgement/closed-position receipt, not by
an unrelated no-order receipt or by assuming an absent order proves no dispatch.

## Scalper

The reviewer receives the native execution intent only after quote reconfirmation,
current account/risk sizing and executable-size checks. Its pending result is
represented separately from `authorized` and `submitted`. The coordinator retains
the symbol claim, stores the candidate identity and does not count a trade attempt.

Repeated assessments for the symbol cannot create another review, including when
the observed generation changes. A supplied `paper_entry_review_refresher` can
refresh/revoke authorization from current snapshot/assessment evidence while the
review waits. The trusted producer must refuse changed/ineligible structures and
respect the original candidate expiry; an unrefreshed lease expires automatically.

## Owner-lane completion pump

`NativeGrokReview.apply_resolutions(warrior, scalper, at=...)` drains acknowledgements
through the existing native management paths and calls each engine's
`resolve_native_review` on the serialized owner lane. It retains a bounded
`NATIVE_RECEIPT_PENDING` backlog until bookkeeping succeeds. A failed handler
blocks new review batches; retries concern management/bookkeeping only.

Canonical ACKNOWLEDGED receipts update authorization/submission provenance without
regressing an already entered, protected, managing, exiting or terminal opportunity.
Scalper attempts are counted at acknowledgement. NO_ORDER receipts release claims
and return pending opportunities to WAIT; they do not count trade attempts.
POSITION_CLOSED receipts require a canonical terminal order and zero inventory,
release claims and complete the original opportunity. Unfilled cancelled orders
do not reset the Scalper loss streak. Already accounted closed lifecycles do not
record the same loss twice.

The pump rechecks canonical closure when a retained acknowledgement becomes flat
before its bookkeeping retry. It does not revive that position or issue a buy.
Uncertainty retains ownership and prevents fallback to the synchronous submitter.

## Remaining desktop integration

The shared review adapter, native pending branches and completion consumers are
now present. Desktop still needs the explicit producer/refresher callbacks,
current authorization derivation, shared completion pumping and activation
configuration. These callbacks must use current native setup, tradability, halt,
session and execution-permission evidence; they cannot be unconditional grants.
No Atlas launch, paid model request, LIVE trading or automatic Grok activation
occurs in this stage.

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-native-branches-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_native_review_branches.py `
    tests/asset_modules/test_grok_native_review.py `
    tests/warrior_momentum/test_quick_scalper.py `
    tests/warrior_momentum/test_quick_scalper_runtime.py `
    tests/warrior_momentum/test_opportunity_engine.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Native review branch tests failed." }
```
