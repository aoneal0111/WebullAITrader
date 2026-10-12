# Crypto shared PAPER allocation

`CryptoAllocationRouter` is an optional offline handoff between one explicitly
selected `CryptoPaper` ledger and a configured shared account allocation. It is
not injected into desktop composition or the live crypto supervisor in this
stage. No data, strategy promotion, broker calls or Grok activation is added.

## Entry authority and costs

Entries default off and require an exact `True` from a trusted deterministic
authorization callback. The callback must check allowed venue/pair/tradability
and other entry policy; it is not a slow model call. The ledger keeps its native
spot-only sizing, fee/slippage, reward, cash, risk and position limits.

`CryptoEntryReservation` reserves spot notional plus modeled entry/exit costs;
it cannot use the futures margin model. Quantity retains eight fractional
places. Shared capital and loss reservations round upward to eight USD decimal
places, so precision is not hidden by rounding down. Native prepared quantity,
price, costs, timestamp and proposal fingerprint must match at dispatch. A
changed quote causes a known refusal rather than filling against old sizing.
Shared quote freshness remains two seconds, stricter than the standalone
crypto simulator's existing 90-second policy.

Crypto commands compete with equity, options and futures under the same
explicit account ID. Separate account IDs do not pool cash. Broker buying power
and cross-venue transfers are not inferred. A bound ledger requires its router
capability for new BUY fills, but SELL/protection remain available when entries
are off. The supervisor is not automatically redirected to this router; the
current default unbound desktop crypto ledger continues its prior behavior.

## Recovery and settlement

The ledger's allocation binding survives restart. Existing positions require
matching owned shared commands and local evidence before binding; standalone
or legacy unlinked exposure cannot be silently adopted. Local IDs map to
ledger-scoped hashed shared command IDs, preserving the original dispatch ID.

Reserve and claim before native apply. Matching OPEN evidence recovers a lost
acknowledgment. Only the explicit native `CryptoEntryRefused` class can release
a known refused dispatch, with no committed entry evidence. Other exceptions
leave SUBMITTING reserved; absence is UNCERTAIN, not a refusal, and there is no
automatic retry/restart. Identity, original quantity, actual entry cost and
fingerprint must match recovery evidence.

Partial exits keep the full reservation held. Their local realized results
accumulate until final closure, when the whole entry's net P&L is booked once
and capital is released. Pending partial P&L is not credited to shared capital.
Matching final settlement recovers after an interrupted settlement operation;
generic flat reconciliation cannot bypass it.

Verified final crypto/options/futures results contribute to one simulator P&L
adjustment. The ceiling is original configured capital plus the lesser of total
simulator P&L and zero: gains offset losses but never compound beyond the
configured cap. Equity realized P&L is still not booked into this settlement
model. Crypto final settlements preserve bounded fractional precision (up to
40 digits and 32 fractional places) independently of the eight-place reservation
limits; options/futures settlement validation remains unchanged. New commands
cannot backdate before the latest verified simulator settlement.

Cross-database dispatch remains non-atomic. Conservative holds are intentional.
Protection must keep receiving current quotes and calls to the router for
settlement reconciliation. No automatic background supervisor is introduced.

## Windows mechanics check

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-allocation-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_crypto_allocation.py `
    tests/asset_modules/test_crypto_durable_intents.py `
    tests/asset_modules/test_crypto_quote_ownership.py `
    tests/asset_modules/test_markets.py `
    tests/asset_modules/test_supervisor.py `
    tests/asset_modules/test_futures_allocation.py `
    tests/asset_modules/test_options_allocation.py `
    tests/asset_modules/test_admission_controller.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto allocation tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_allocation_check
if ($LASTEXITCODE -ne 0) { throw "Crypto allocation check failed." }
```

The check uses fabricated quotes and temporary ledgers. Equity reservation
blocks crypto entry; releasing that unsubmitted reservation lets crypto open.
A target exit with new entries disabled frees all shared commands. Synthetic
P&L demonstrates mechanics only, not profitable trading or actual fills.
