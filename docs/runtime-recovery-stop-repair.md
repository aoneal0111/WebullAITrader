# Cooperative stream Stop and recovery diagnostics

## Fixes

The official SDK connection wait normally lasts ten seconds. Market-data Stop
joined the consumer for five seconds without cancelling its in-flight recovery
connection. A failed join could report a runtime error during an intentional
shutdown. The transport has a local request_stop path that does not acquire
the connection/session lock. SDK connection waits poll the cancellation event
at most every 50 ms. The watchdog refuses new recovery after Stop and local
cancellation is distinguished from a provider failure. Late connection callbacks
cannot revive a stopped backend. A new runtime constructs a fresh transport.

This does not interrupt arbitrary synchronous SDK HTTP calls or guarantee a
maximum shutdown time. Native Grok acknowledgement reconciliation, report
workers and other genuine cleanup failures retain their behavior.

A subsequent failure during operator Stop records RUNTIME_CLEANUP_FAILED and
its bounded exception class while preserving the initiating operator origin
and component. Foreign-session failures cannot change the active stop.
Connection diagnostics retain safe SDK error code/HTTP status and distinguish
API-client identity mismatch from session-identifier mismatch without recording
raw session IDs or error bodies. Watchdog reconnect starts increment the same
lifecycle attempt counter as transport-driven recovery.

## Verification and deployment

Threaded tests hold an SDK connect acknowledgement pending with a 30-second
configured timeout and prove Stop joins the connection thread within one second.
Other tests cover cancellation before consumer join, no watchdog recovery after
Stop, provider failure retention, redaction and first-origin preservation.

The updater runs focused recovery/shutdown suites before a fast-forward and
creates a backup branch. Runtime-review capture requires an explicit artifact
path or run ID to avoid mistaking installer tests for a production session.
The capture performs no provider requests and restarts no runtime.

Strategy settings, Grok policy, operating spec, ledgers, launchers and crypto
workers are unchanged. The Warrior quote-routing and bar-recovery repair is
retained. This does not establish profitable trading or resolve every provider
failure. Production verification remains necessary. Private artifacts must not
be committed to the repository.
