# Periodic PAPER protection feed monitor

`PaperProtectionMonitor` supplies the periodic caller required by
`PaperProtectionFeed.check_health`. Construction starts no thread. Explicit
`start()` creates one interruptible daemon thread; duplicate starts are
idempotent. A feed can be claimed by only one monitor. The poll interval is
0.01 through 1 second and no more than half that feed's silence limit.

The monitor checks immediately and periodically even when no events arrive.
Silence revokes the feed generation and pauses matching through the existing
feed gate. A transport owner may subsequently reconnect that feed, but fresh
status is still required. The monitor does not infer tradability, reconnect,
subscribe, match prices, authorize entries or contact any provider itself.

A health-check exception or clock failure terminally closes the feed and marks
the monitor FAILED. Raw errors and provider data are not retained in snapshots.
Thread-start failure also revokes the feed. Recovery needs a new feed and
monitor, not a restart of the failed instance.

`close(timeout_seconds=2)` wakes the monitor, terminally revokes callbacks and
joins its thread. Repeated closure is safe. The join allowance is bounded to
zero through five seconds; false means shutdown has not completed or feed
closure failed. This is cooperative supervision: feed/host locks and underlying
I/O can block, so the allowance is not a hard end-to-end shutdown deadline.
The monitor cannot recover a killed process or hung host.

Lifecycle owners must stop their transport, close the monitor successfully,
then close the host. Monitor closure preserves the host, ledger ownership,
inventory and durable protective orders. It never flattens positions or cancels
their exits. A closed feed cannot reconnect or accept callbacks; a replacement
feed starts paused and requires fresh status. Do not release host ownership
while a monitor thread is still running.

Snapshots report only local monitor state and its last observed feed state;
reading them does not perform a health check or validate a real feed.

The existing `python -m app.paper_trading.protection_host_check --synthetic`
now exercises periodic silence detection and terminal callback rejection in
the separate protection process, without Qt, network calls or user-ledger access.

This monitor is not installed into desktop startup or Windows Services. A
verified provider adapter with real quote sizes, timely halt/status delivery,
subscription recovery and external process supervision is still required for
unattended use. No online protection launcher is enabled by this change.
