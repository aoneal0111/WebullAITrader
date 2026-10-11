# Native pending-review boundary

`NativeGrokReview` is an explicit producer adapter for original Warrior and
Scalper equity entries. It prepares the native placement through the shared
bridge builder, binds the original Warrior context, and submits a single bounded
batch to the background Grok handoff. It does not install itself into desktop
composition or change the existing synchronous entry callbacks.

`NativeReviewReceipt.pending` means `PENDING_REVIEW`, never authorization,
submission or a fill. Receipts reject implicit boolean conversion so an existing
boolean submitter cannot mistake pending work for an accepted order. A producer
must retain its native ownership claim while pending, avoid resubmitting the
lifecycle, and handle the eventual resolution explicitly on its owning lane.

## Current authorization

`CurrentEntryAuthorization` is trusted runtime evidence bound to the exact
lifecycle. It requires typed setup-confirmed, tradable, halt-clear,
session-allowed and execution-permitted checks and aware timestamps. Its lease
expires after two seconds or its validity deadline, whichever is earlier.
A producer must refresh it from actual current native evaluations. Grok cannot
create, edit or refresh this evidence.

The authorization callback checks the exact retained prepared object before
review and again before dispatch. Refresh cannot rebind the lifecycle, rewind
observation time, accept future evidence or extend the original candidate
expiry. False checks revoke authorization. No authorization lock covers an AI
request or controller dispatch. Quotes, account and risk are still checked by
the controlled PAPER service at dispatch. Slow reviews can be refused when the
producer has no fresh evidence; freshness is not weakened to accommodate AI.

## Completion ownership

`drain(warrior, scalper)` runs on the serialized runtime lane. Canonical bridge
protection remains independent of model review. The bounded state projection is:

| State | Producer behavior |
| --- | --- |
| PENDING_REVIEW | Keep ownership; do not report an order or retry. |
| ACKNOWLEDGEMENT_PENDING | Canonical result retained; retry management adoption only. |
| RECONCILIATION_REQUIRED | Dispatch uncertain; retain ownership and block retry. |

`take_resolutions()` consumes each completed receipt once:

| Resolution | Meaning |
| --- | --- |
| NO_ORDER | Explicit abstention/refusal; no successful or uncertain dispatch remains. |
| ACKNOWLEDGED | Canonical acknowledgement adopted into native management. |
| POSITION_CLOSED | Canonical terminal order and zero remaining inventory; no state revival. |

Unread resolutions retain producer bindings and block the next batch. There is
no accumulating history or queue: at most 32 bound entries and their receipts.
An uncertain result does not become a NO_ORDER receipt just because an order is
absent. A controller-unavailable result is conservatively retained: the native
service may have dispatched successfully before a ledger acknowledgement failed.
Malformed success completions without a canonical result are also retained. Stop revokes further authorizations immediately, but late acknowledgements
remain adoptable. `close` stays incomplete until the worker has stopped and the
owner consumes outstanding resolutions.

## Activation boundary

This stage provides the shared producer adapter and pending/completion contract.
Native Warrior/Scalper evaluation branches must still retain/release their own
ownership and map these receipts to opportunity/funnel transitions. Default
desktop callbacks remain synchronous, and automatic Grok trading remains off.
No LIVE trading, Atlas launch, paid Grok request or code rewriting is enabled.

Offline verification:

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-native-review-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_grok_native_review.py `
    tests/asset_modules/test_grok_desktop_completions.py `
    tests/asset_modules/test_native_entry_preparation.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Pending review tests failed." }
```
