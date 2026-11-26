# Equity stream recovery and transient account reads

Reconnect timeouts previously left the scanner disconnected while its failed
SDK connection could remain open. Failure cleanup also stopped the accepted
authoritative lane, unlike successful recovery. The recovery path now explicitly
retires failed transports even when the coordinator never set connected=true,
using the same bounded disconnect window while retaining the authoritative lane.

A pending SDK disconnect blocks a new connect or another disconnect operation;
it cannot overlap a newer session. Retired-client connect/disconnect callbacks
cannot invalidate the current SDK identity. Failed Webull connect marks adapter
health disconnected and records the underlying exception class. Watchdog failures
also populate stream failure diagnostics, and a fresh recovery payload updates
the reconnect result. Connection success alone still does not authorize entries.

In PAPER mode only, a typed retryable Webull NetworkError or TimeoutError from an
account read is retried at most twice, with interruptible one/two-second waits.
No snapshot or account timestamp is refreshed for a failed attempt. Existing
account age and native risk checks remain in force. Authentication, validation,
non-retryable errors, live-account reads, and repeated failures retain terminal
failure behavior. Orders and mutations are never retried by this change.

A successful REST poll no longer announces runtime RUNNING while the feed is
stale or recovering. Morning status labels a closed recorder's engine state as
historical and lists RECORDING_CLOSED as an entry blocker. It cannot verify a
running process from saved readiness alone.

The installer requires Atlas to be closed, runs focused recovery and protection
checks in an isolated checkout, backs up the branch, and fast-forwards source.
It retains the existing trial, its original deadline, paid-request budget,
launchers, ledger and environment file. It starts nothing and leaves crypto
containers alone. This patch does not establish the external cause of Webull
silence or guarantee uninterrupted provider connectivity.

Synthetic regressions cover a timed-out reconnect followed by recovery on the
same authoritative lane, bounded account retry, operator cancellation, rejection
of obsolete SDK callbacks, and prevention of overlapping SDK teardown/connect.
The broader run also encountered five legacy manual-GUI admission tests that
fail on the unchanged base revision; no production qualification gate was relaxed
to satisfy those expectations.

The stop-conflict counts require order/fill evidence to classify; this network
repair does not claim that every earlier stop rejection was harmless or fixed.
