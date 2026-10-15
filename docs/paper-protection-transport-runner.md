# PAPER protection transport runner

`PaperProtectionRunner` connects the inventory host, feed gate and periodic
monitor to injected quote and status transports. It starts no GUI, entry engine
or model. Construction pauses matching; `run(stop_event)` is single-use and
owns its transport resources, while the caller retains the host and ledger lock.

The current Webull integration is **not enabled** by this change. The successful
SPY/QQQ quote-field probe established sampled timestamps, prices and reported
sizes. It did not establish share units or a verified halt/resume channel.
There is deliberately no online launcher or configuration switch for this runner.
Missing either adapter returns `BLOCKED_UNVERIFIED_ADAPTERS` before networking.

## Trusted adapter contract

The backend factory must create a fresh, exclusively owned backend per attempt.
It implements `connect()`, `subscribe(symbols)`, bounded `receive()`,
`disconnect()`, `registration_ready` and `generation_metrics['generation']`.
The existing official SDK backend has this interface; its use here still requires
the two independently reviewed adapters below. Do not share that backend with
another consumer that drains its queue.

`quote_decoder_factory(symbols, generation)` returns a callable that emits only
canonical QUOTE events, or `None` for ignored messages. It must enforce provider
generation, requested symbols, aware provider/receipt/dequeue timestamps and book
validation, and normalize quantities to **confirmed shares**. The existing
`ProtectionQuoteDecoder` reports unverified provider quantities; passing it
directly does not meet this contract. No guessed lot multiplier is supplied.

`open_status(symbols, emit)` subscribes afresh and returns a handle with `close()`
and a boolean `healthy` property. It must clean up its own resources if opening
raises. It must establish current instrument tradability and reliable halt/resume
delivery, including gaps/replay/reconnect handling. It must become unhealthy on
loss of that evidence, not merely report that a socket exists. Only verified,
timestamped canonical RESUME/TRADING_HALT events may reach `emit`. Profile
permission `OC`, connection notices and quotes do not qualify. Both reviewed
adapters route under the runner's explicit common source; that label is not
authentication or proof of provenance.

Provider references for the outstanding verification:

- [Market data streaming](https://developer.webull.com/apis/docs/market-data-api/data-streaming-api/)
  describes quote quantities and reconnection subscriptions, but does not
  establish share units for the observed SDK quantities.
- [Broker instrument events](https://developer.webull.com/apis/docs/reference/fd-events/instruments-events/)
  describes instrument property and trading-permission changes. Individual-account
  channel access and exchange halt/resume semantics remain unverified.

## Lifecycle and failure behavior

- Subscribe only to `host.protection_symbols`, an immutable sorted snapshot of
  owned remaining inventory/working orders. Flat hosts create no transport.
- Connect/register and subscribe before opening a new feed generation and status
  subscription. Each generation starts paused until fresh verified status arrives.
- Check quote registration/generation and status health before and after every
  bounded receive. Discard a queued quote if either channel lost health.
- Monitor silence independently of incoming messages. Invalid decoded events,
  transport errors, expired feed authority or status-channel loss retire the
  generation and pause matching before cleanup.
- Default to three connection attempts (maximum five), with an interruptible
  one-second delay. Reconnect re-subscribes and requires fresh status. Old
  callbacks cannot authorize the replacement generation.
- Revoke the generation before closing status and quote transports. Cleanup
  failure prevents another attempt. Finally close/join the monitor. A failed
  shutdown reports `SHUTDOWN_FAILED`; callers must retain host ownership until
  resource shutdown has been resolved. The runner never closes the host itself.

Adapter open/close and backend calls must be bounded/cooperative. The monitor
cannot interrupt blocked host I/O or restart a dead process. External process
supervision, actual provider contracts and integrated real-feed validation are
still required. This runner supplies PAPER mechanics, not evidence of profitable
strategies or unattended protection readiness.

## Verification

Run `tests/paper_gateway/test_protection_runner.py` together with the existing
host/feed/monitor/quote tests. Tests use disposable seeded ledgers and synthetic
adapters, including partial exits, reconnects, registration/status loss, silence,
stale callbacks, interrupted retries and cleanup failures. They contact no
provider and never open the user's trading ledger.
