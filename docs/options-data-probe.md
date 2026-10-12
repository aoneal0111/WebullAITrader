# Read-only Webull options data boundary

The installed official Webull SDK 2.0.14 has these read-only interfaces:

- `DataClient.instrument.get_option_contracts(category='US_OPTION', underlying_symbols=..., status='LISTING', page_size=10)`
- `DataClient.option_market_data.get_option_snapshot(symbols=[...], category='US_OPTION')`

The SDK uses v2 `/openapi/instrument/option/contracts` and
`/openapi/market-data/option/snapshot`. Current Webull docs describe v3
`/trading/instruments/options/contracts/list` and
`/market-data/options/snapshots/list`. We do not change SDK dependencies or
invent request/response compatibility to bridge that difference.

`OptionsDataProbe` lazily calls only the installed SDK discovery/snapshot methods.
It accepts SPY/QQQ for discovery and standard SPY/QQQ OCC contract symbols for
snapshots. Discovery asks for ten contracts per underlying. At most three market
read attempts are permitted per probe instance: two discovery calls and one
snapshot batch with at most twenty symbols. Failed initialization counts toward
the bound. SDK authentication may make its own requests; the read-attempt counter
is not a count of all HTTP/token exchanges. No retries, streaming subscription,
order calls, model requests, ledger fills or desktop activation are introduced.

## Safe diagnostic sample

Successful responses produce a bounded schema sample, not executable quotes.
Only the first two items of each array are sampled; discovery is not a complete
options chain. Selected market fields retain finite numeric values, timestamps
and supported contract symbols. Sensitive fields are removed, arbitrary string
values become type descriptions, and non-success HTTP bodies are never parsed
or printed. HTTP statuses remain generic (401/403 can indicate authentication or
access failure; they do not prove the exact subscription issue).

Missing or undocumented bid/ask source times must not be replaced by request
receipt time. `quote_readiness=UNVALIDATED_RESPONSE_SCHEMA` remains explicit even
for a successful HTTP response. Expiry, multiplier, tick, deliverables, separate
bid/ask provenance and data freshness still require actual-response verification
before conversion into the native option Contract/Quote types.

## Offline check

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.options_data_check
```

Default execution reads the installed SDK version only: no dotenv loading,
credentials, SDK initialization or network access.

## Explicit online capture

With Atlas stopped, use the existing scoped Webull market-data configuration:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.options_data_check --online
```

This loads the same project dotenv/process configuration used by Atlas, consumes
market-data credentials, and initializes the existing official SDK client with a
five-second transport timeout. It makes the bounded discovery/snapshot reads.
It does not modify configuration or execution databases. Share the diagnostic
JSON, never the key/secret or raw signed SDK requests.

Webull currently requires OPRA Real-Time Non-display permission for real-time
OpenAPI options quotes; subscriptions in its app/desktop are independent.
Sandbox data can be delayed. A recent receipt does not make delayed or old
source data eligible for paper fills. Do not purchase a subscription based only
on a generic failed response: identify access and source provenance first.

Primary references checked October 12, 2026:
- https://developer.webull.com/apis/docs/reference/option-contract-list/
- https://developer.webull.com/apis/docs/reference/option-snapshot/
- https://developer.webull.com/apis/docs/market-data-api/overview/
- https://github.com/webull-inc/webull-openapi-python-sdk (reference HEAD a485198b19a017ced58341ce0950093b7e0efae1; installed 2.0.14 source verified separately)

The next stage needs this response sample and entitlement result to implement
validated contract discovery and a fresh quote adapter. Options remains offline
planning/paper-ledger only until those boundaries and desktop routing are ready.

## Isolating a failed snapshot

If discovery succeeds but a combined snapshot returns SDK_REQUEST_UNAVAILABLE,
that state alone does not identify entitlement, transport, protocol or symbol
failure. The probe now includes exception class, typed HTTP status where present,
and only known SDK/provider error codes. Exception messages, request IDs and
unknown provider codes remain suppressed.

Read one or two known contracts independently, without rediscovering the chain:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.options_data_check --online `
    --symbols "SPY261218C00640000,SPY261218P00690000"
```

These symbols were returned by the operator's October 12 discovery capture;
they are diagnostic examples, not a contract-selection recommendation. This
mode makes at most two snapshot attempts, one per contract, and reports each
requested symbol. It never retries a failed read, requests a subscription, places
an order or upgrades the SDK. The global three-attempt bound remains unchanged.
Quote readiness still remains unvalidated after a successful read.
