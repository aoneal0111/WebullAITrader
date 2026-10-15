# Read-only protection quote evidence

`ProtectionQuoteDecoder` normalizes the pinned Webull SDK 2.0.14's decoded
`QuoteResult` inside Atlas's received-payload envelope. It requires the expected
backend generation, a requested equity symbol, explicit provider milliseconds,
aware receipt time, and fresh provider/receipt times within five seconds.
Provider time after receipt is rejected. No timestamp is replaced by local time.

The SDK's `BasicResult.timestamp` stores integer milliseconds. Its convenience
UTC getter removes timezone information, so this decoder reads the integer
directly. Book prices and sizes are bounded finite numbers; missing sizes are
not synthesized and zero reported size remains zero. Up to ten ordered levels
are validated. Only equity `quote` topics are accepted. Snapshots, trades,
notices, heartbeats and event-contract quotes supply no protection quote or
status authority. Raw/compressed input is not decoded here; the existing SDK
backend supplies decoded objects. Generation/source checks are not provider
authentication or entitlement evidence.

The decoder can supply canonical QUOTE events to the existing protection gate,
which independently enforces connection generation, status, freshness and
duplicate handling. A temporary-ledger integration test verifies that a quote
cannot resume a paused host and that, after synthetic test-only status, actual
reported size caps matching. No production status adapter is added.

## Operator check

The CLI never opens a trading ledger, constructs a protection host, runs an
entry engine or submits an order. Offline is the default and does not load
configuration or construct a client:

```powershell
& .\.venv\Scripts\python.exe -m app.paper_trading.protection_quote_check
```

Explicit read-only online observation uses existing **market-data** configuration
and credentials, preserves environment precedence, and requires TLS:

```powershell
& .\.venv\Scripts\python.exe -m app.paper_trading.protection_quote_check `
    --online --symbols "SPY,QQQ" --seconds 10
Write-Host "QUOTE_CHECK_EXIT=$LASTEXITCODE"
```

It attempts one SDK connection and one QUOTE subscription, with no application
reconnect loop. The existing backend may perform bounded registration recovery;
operation counts are not wire-request counts. The read window is 1..30 seconds,
at most 500 messages (default 200), with at most five symbols and one public
quote sample per symbol. Observation stops once every requested symbol has a
valid quote. SDK connection/registration and cleanup are additional cooperative
operations; the read window is not a hard end-to-end I/O timeout.

Exit zero / `QUOTE_FIELDS_OBSERVED` means fresh timestamped book fields were
observed for all requested symbols. It does not establish continuous coverage,
size units, consolidated quotes, halt delivery, subscription recovery or
protection effectiveness. Provider-reported size units are explicitly unverified.
Missing, stale, malformed and partial input is counted without raw provider
errors, credentials or instrument identifiers. Cleanup is attempted on failures.
`status_authorization` remains NONE and `live_feed_validated` remains false.

## Source boundary, reviewed October 15, 2026

- [Market-data streaming](https://developer.webull.com/apis/docs/market-data-api/data-streaming-api/)
  documents quote book sizes and Basic timestamps. Its JSON status notice is
  connection telemetry, not exchange halt/resume evidence. Subscriptions must
  be restored after reconnect.
- [Broker instrument events](https://developer.webull.com/apis/docs/reference/fd-events/instruments-events/)
  describe OC/CO/NT trading-permission changes. They do not establish this
  account's access to a timely exchange halt/resume channel.
- [Individual trade events](https://developer.webull.com/apis/docs/reference/custom/subscribe-trade-events/)
  describe account order-state events, not a replacement halt channel.

A verified status source and process supervision are still required before an
online protection launcher can be enabled. This check does not install one.
