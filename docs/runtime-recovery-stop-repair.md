# Cooperative stream Stop and recovery diagnostics

The November 16 review shows 561 failed reconnects and an operator Stop that
finished in 10.7 seconds with failure=true but no retained exception class.
Neither observation establishes a single underlying Webull outage cause.

## Fixes

The official SDK connection wait normally lasts ten seconds. Market-data Stop
joined the consumer for five seconds without cancelling its in-flight recovery
connection. A failed join could report a runtime error during an intentional
shutdown. The transport now has a local request_stop path that does not acquire
the connection/session lock. SDK connection waits poll the cancellation event
at most every 50 ms. The watchdog refuses new recovery after Stop and local
cancellation is distinguished from a provider failure. Late connection callbacks
cannot revive a stopped backend. A stopped backend is terminal; a new Atlas
runtime constructs a fresh transport.

This does not interrupt arbitrary synchronous SDK HTTP calls or guarantee a
maximum shutdown time. Native Grok acknowledgement reconciliation, report
workers and other genuine cleanup failures retain their existing behavior.

A subsequent failure during operator Stop now records RUNTIME_CLEANUP_FAILED
and its bounded exception class, while preserving the initiating operator
origin and component. Foreign-session failures cannot change the active stop.
Connection diagnostics retain safe SDK error code/HTTP status and distinguish
API-client identity mismatch from session-identifier mismatch without recording
raw session IDs or error bodies. Watchdog reconnect starts now increment the
same lifecycle attempt counter as transport-driven recovery.

## Verification and deployment

Threaded tests hold an SDK connect acknowledgement pending with a 30-second
configured timeout and prove Stop joins the connection thread within one second.
Other tests prove cancellation before consumer join, no watchdog recovery after
Stop, real provider failure retention, redaction, and first-origin preservation.
The updater runs the focused recovery/shutdown regression suites on Windows
before a fast-forward and creates a backup branch. It then captures the three
latest existing full performance JSON files into a compact private review ZIP.
The capture performs no provider requests and does not restart any runtime.

Strategy settings, Grok policy, paper operating identity, ledgers, launchers and
crypto workers are unchanged. The earlier Warrior subscribed-quote and bar
recovery repair is retained. This release does not establish profitable trading
or resolve every provider reconnect failure. Review the private ZIP to identify
the original failed stage, then verify fresh payload recovery during the next
operator-started paper session. No raw private diagnostic belongs in git.
