# Native entry preparation for Grok review

`AutonomousPaperExecutionBridge.prepare_entry_decision` runs the same bridge
entry gates and command builder used by synchronous Warrior and Scalper entries.
It returns `PaperEntryPreparation` containing the original placement and a
`PREPARED` decision, or a refused decision with no placement. `PREPARED` is not
`AUTHORIZED`, and `submission_attempted` stays false. Preparation performs no
order placement, capital reservation, durable opportunity consumption, or new
active lifecycle claim. Existing terminal reconciliation still runs.

Quantity, limit, structural stop, risk, ownership, opportunity, taxonomy and
Scalper economic metadata come from the shared native builder. Retain the exact
placement for `PreparedEntry.from_placement`; rebuilding it would change its
client identity. Repeated preparations are not deduplicated reservations: the
runtime pending-review boundary must keep one bound candidate per lifecycle.

Native original commands carry `opportunity_deadline` before dispatch. The
controller uses `chase_deadline` when present, otherwise `opportunity_deadline`.
The Grok prepared-entry factory now follows this same precedence without editing
the placement. Canonical acknowledgement adoption verifies the original
opportunity deadline and the controller's enriched chase deadline against the
same original expiry, in addition to existing command and metadata signature
checks. It cannot extend an opportunity or adopt a mismatched acknowledgement.

The original synchronous submission API remains intact. Preparation is a
prerequisite for runtime signal producers; it does not connect them or enable
Grok trading. Production review still requires current deterministic strategy,
session and halt authorization before and after model review, followed by the
controlled PAPER service's current account and execution quote checks.
The separate pending-review transition and desktop activation remain subsequent
work. No Atlas process or paid model request is needed for these offline tests.

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-native-prepare-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_native_entry_preparation.py `
    tests/asset_modules/test_grok_entry_ownership.py `
    tests/asset_modules/test_grok_desktop_completions.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Native preparation tests failed." }
```
