# Webull status evidence before headless PAPER feed activation

The headless PAPER core and feed lifecycle gate are mechanics-tested, including
Windows subprocess takeover and reconnect checks. They are not connected to a
production feed. `protection_status_check` adds a bounded, read-only observation
of stock-profile status, without acquiring the runtime lock, opening a ledger,
creating orders, starting Atlas or making model calls.

## Source review — 2026-10-14

Primary sources reviewed:

- [Webull Data Streaming API](https://developer.webull.com/apis/docs/market-data-api/data-streaming-api/)
- [List Stock Instruments](https://developer.webull.com/apis/docs/reference/instrument-list/)
- Installed `webull-openapi-python-sdk==2.0.14`, specifically
  `webull.data.quotes.instrument.Instrument.get_instrument` and
  `webull.data.common.instrument_status.InstrumentStatus`.
- Atlas `app/webull/market_event_parser.py` and
  `app/webull/sdk_market_data.py`.

The streaming guide documents QUOTE, SNAPSHOT, TICK, NOTICE and ECHO topics.
Its JSON notice example uses `type: status` with connection statistics
`rtt`, `drop` and `sent`. That example is not a security halt/resume event.
The quote/snapshot/tick schemas shown do not establish a halt-status channel.
This is a limitation of the reviewed evidence, not proof that Webull has no
additional channel. The guide also requires re-subscription after reconnect.

The installed SDK defines instrument status 1=ACTIVE, 2=INACTIVE, 3=HALTED.
It exposes `get_instrument`; the newer reference SDK's `list_instruments` is
not available in the installed 2.0.14 package. This diagnostic deliberately uses
the installed method, with explicit symbols, category US_STOCK and page_size=5.
It applies no status filter, so an inactive/halted requested row is not hidden
by an ACTIVE-only query. It neither upgrades the SDK nor assumes that current
documentation and the installed endpoint have identical response envelopes.

## Diagnostic contract

Offline is the default and performs zero requests. Explicit `--online` loads
the existing market-data credential scope from configuration and makes at most
one profile SDK operation per probe instance, including failed initialization.
The configured SDK timeout is five seconds and SDK request retries are disabled
by the existing client builder. `sdk_operation_attempts` counts logical reads;
SDK authentication may involve additional HTTP traffic. This is not an exact
wire-request counter or a hard end-to-end process deadline.

The probe accepts one to five unique uppercase equity symbols. It examines
top-level lists or a `data` list, with a 128 KiB response limit and at most 100
rows. At most ten requested-symbol samples are printed. Unexpected symbols are
counted without their values; unknown envelopes remain UNKNOWN. Pagination is
reported but never followed. Duplicate returned rows remain visible as samples;
no row is selected as authoritative. Credentials, arbitrary provider strings,
instrument identifiers, pagination values and error bodies are not printed.

Only recognized status codes/labels are retained. Missing, unknown, boolean or
floating-point status values remain UNKNOWN. Selected date/epoch field syntax
is shown without asserting timestamp units, provider clock freshness or that an
update time represents a status transition. A profile response's receipt time
is a local observation, not an exchange status-event timestamp.

```powershell
& .\.venv\Scripts\python.exe -m app.paper_trading.protection_status_check
& .\.venv\Scripts\python.exe -m app.paper_trading.protection_status_check --online --symbols "SPY,QQQ"
```

Exit zero online means a bounded JSON response was captured, even if its status
fields are missing or unknown. It does not establish halt delivery, entitlement
freshness or tradability. `status_authorization` is always NONE and
`live_feed_validated` is always false. No RESUME event is constructed.

Next, inspect the actual response fields and obtain evidence of timely
symbol-level status changes, initial status and reconnect recovery. A profile
ACTIVE value alone does not satisfy those requirements. If the documented
market feed cannot supply them, resolve the status-source contract before
activating an unattended matcher. The GUI remains the existing runtime owner;
no background protection service or read-only GUI mode is installed here.
