# Official SDK stream worker failure handoff

The supported Webull SDK starts connect_and_loop_forever in a background
thread. Its initializer runs before the SDK's internal retry loop. If that
initializer raises, no connection-success callback arrives. Waiting only for
that callback reports a timeout even though the original SDK worker has
already failed. The same visibility gap exists for fatal worker exits and
socket failures retained by the SDK's internal retry loop.

## Repair

Each owned SDK instance now retains safe initialization/socket-stage diagnostics
and hands fatal worker exceptions back to Atlas's connect/receive owner lane.
A failed worker clears connection, registration and subscription readiness.
Late success callbacks cannot revive it. Retired client/generation failures do
not poison its replacement. Explicit Stop retains its cancellation behavior.
There is no process-wide thread hook or change to SDK retry policy.

Stage failures retain only exception class, SDK error code, HTTP status and
cause class. Raw error text, session identifiers, request bodies and credentials
are not exported. The original exception remains in memory for exception
chaining. A real deadline with a still-running SDK worker remains a timeout;
its last startup phase and safe stage failure are separately reported.

## Read-only workstation check

Check-Webull-Stream.ps1 loads existing configuration and calls only the
market-data stream factory. It requires PAPER configuration with live trading
disabled, connects, subscribes to one stock, observes for a bounded window,
and requests disconnect. It does not construct the desktop, broker account
poller, Grok or an order runtime. SDK connection and cleanup operations have
their own timeouts, so the observation window is not a total process deadline.
No payloads while the exchange is inactive does not establish a failure when
connection and subscription succeeded.

The installer runs offline tests and leaves trading stopped. Its explicit
RunConnectionCheck switch performs the read-only provider check after install;
without the switch no provider request is made. The report contains safe
connection stage/failure identity and normalized event counts. Existing settings,
strategy parameters, ledgers, launchers and crypto workers are preserved.

Tests exercise the actual SDK worker with a failing initializer and no network
access, fake socket/retry failures, active-session failure, retired-worker
isolation, redaction, read-only probe scope and unsafe-mode refusal. This
repairs error propagation; it does not prove that a particular provider outage
or network restriction has been resolved. Workstation verification is required.
