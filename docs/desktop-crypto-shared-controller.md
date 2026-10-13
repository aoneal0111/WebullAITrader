# Desktop crypto shared PAPER controller

`ATLAS_CRYPTO_SHARED_ALLOCATION_ENABLED=true` attaches the existing crypto
supervisor to the desktop's campaign-bound PAPER admission controller. It
requires `ATLAS_PAPER_CONTROLLER_ENABLED=true`, PAPER environment, disabled live
trading, and an actual composed `ControlledPaperTradingService`. Invalid
configuration fails before the crypto ledger is opened. The flag defaults to
false; construction never starts workers or enables AI proposals.

The same controller instance and account ID serve equity and crypto. The existing
crypto cash ledger is still separate; shared allocation is a budget for simulated
entry exposure, not a broker cash balance or a transfer between accounts. Crypto
retains its native cash, position, loss, fees, slippage and quote checks. The GUI
labels this arrangement explicitly. Options and futures desktop entry composition
is not activated by this flag.

Before a crypto buy, trusted local authorization verifies the equity controller's
immutable campaign and authoritative order history. Unmanaged equity exposure,
unsubmitted reservations, uncertain submissions or conflicting order evidence
block new crypto entries. Known owned equity exposure retains its shared
reservation and competes with crypto under the controller's existing capital and
risk limits. No equity-session or bridge-readiness requirement is applied to
crypto's 24/7 protection. Model proposals cannot change the account binding.

Crypto stop/target protection and final settlement remain independent of AI and
entry permission. The GUI operator-close action reconciles allocation immediately
following native sells. Unknown settlement failures keep reservations rather than
freeing capital without evidence. Synthetic checks do not establish profit or
executable live fills. This change makes no network or paid AI requests.

## Recovery and existing data

The existing `<execution database directory>/crypto-paper.sqlite3` file is used;
no positions, decisions, orders, cash or capture files are deleted. An existing
standalone crypto position cannot be adopted into shared allocation automatically.
Such exposure must finish under its existing management before enabling this
binding. Already bound ledgers recover their matching active commands and restart
with entries off.

Bindings are persistent and immutable. Disabling the flag does not remove a
binding or permit native BUY calls to bypass allocation. Stops and SELLs remain
available. A different campaign/controller/account cannot silently take ownership
of an already bound ledger, even when flat. Campaign rotation requires explicit
reconciliation and a separate ledger migration; do not delete the old ledger to
resolve a binding error. Failure during binding closes the newly opened SQLite
handle and preserves the database. These are the existing router's conservative
recovery rules, now enforced in desktop composition.

## Validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-desktop-crypto-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_desktop_crypto_allocation.py `
    tests/asset_modules/test_crypto_supervisor_allocation.py `
    tests/asset_modules/test_desktop_markets.py `
    tests/asset_modules/test_grok_desktop_configuration.py `
    tests/asset_modules/test_paper_controller_router.py `
    tests/asset_modules/test_crypto_allocation.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Desktop crypto allocation tests failed." }
```

On a later normal operator launch, the binding can be selected in that launch
session with the following settings. These do not start Atlas or enable entries:

```powershell
$env:WEBULL_TRADING_ENVIRONMENT = "PAPER"
$env:LIVE_TRADING_ENABLED = "false"
$env:ATLAS_PAPER_CONTROLLER_ENABLED = "true"
$env:ATLAS_CRYPTO_SHARED_ALLOCATION_ENABLED = "true"
```

Grok credentials remain a separate prerequisite for actual model proposals.
Existing Grok setup and request limits are unchanged. A settings change does not
affect an already running process; preserve protection during normal shutdown.
