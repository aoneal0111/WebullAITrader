# One-way context capture staging

This change adds a capture component, strict private-file reader and canonical
Webull quote adapter. It does not attach either engine to that component, add an
activation flag, start Atlas, or construct an online model client. PAPER strategy
logic, entry qualification, live/Grok order settings and protective exits are
unchanged. Both desktop context hooks and online trial activation remain pending.

`ContextCapture` must be explicitly constructed with a new private output
directory, a frozen baseline fingerprint and a clock. `submit` accepts only a
typed `Snapshot` and matching `StrategyContext`. It validates the complete
evidence and both quote-side ages against current time, then enqueues a bounded
serialized record with `put_nowait`. No disk write, model request or arbitrary
callback runs in `submit`. Queue-full, invalid-input, duplicate, closed, storage
failure and capacity outcomes return false and have separate counters; none can
authorize, delay by waiting for queue space, or retry a trade.

The writer thread only writes a new private file. Limits are 16 queued records,
100 total records, 24 KiB per line and 1 MB total. It never opens installed
ledgers or modifies configuration. Shutdown joins for at most two seconds and
reports whether the writer actually stopped; a failure is not a clean capture.
Call shutdown from lifecycle management, never a protection callback. Local disk
I/O and interpreter scheduling still require measured integration latency before
any desktop attachment. This is not a claim of hard real-time behavior.

The existing canonical Webull execution-quote parser uses `quote_time` or
`quote_timestamp` for both bid and ask, distinct from last-trade time. The new
`webull_quote_snapshot` adapter preserves that timestamp and explicitly returns
`WEBULL_PROVIDER_QUOTE_TIME_SHARED`. It does not invent separate side updates or
substitute receipt time. Shared provider quote time is supported by this capture
contract; independently authored side timestamps are not a new subscription or
readiness requirement. Missing/old timestamps still fail the same freshness
checks. The adapter parses supplied data only; it does not fetch quotes.

Real Webull records require the explicit supported timestamp-basis label; only
recorded fixtures may omit it. These typed labels and fingerprints provide local
traceability, not cryptographic source attestation. A trusted runtime producer
must supply the actual raw provider response, actual engine assessment and bars
known at that decision. The capture component cannot prove that an arbitrary
caller supplied authentic provider data. Unsupported provider adapters remain
unavailable rather than silently reusing Webull provenance.

The strict file reader validates the whole bounded file before any explicitly
injected observer call. Extra fields, duplicate keys/identities, changed baseline,
cross-engine context, out-of-order evidence and malformed bars fail validation.
Loading old records never changes their timestamps. The observer still refuses
stale input at request time and expires delayed advice. No file watcher, tailer,
online client construction, implicit inference or order dispatch is added.

`-GrokCapture` runs offline capture/reader tests. Combine it with `-GrokExport` to
also rerun the read-only saved-evidence archive. The archive now groups rejected
rows by sanitized reason, counts intentionally skipped non-quote events and
separates cap-lookahead rows. No arbitrary exception text is included. Its source
fingerprint list now names the actual `setups.py` file rather than a nonexistent
`strategy.py`. Archive readiness stays false; old records lack sufficient source
provenance and complete contemporaneous context even though shared quote time is
valid for a properly sourced fresh record.

Keep private market/context files local. Share only the sanitized archive
`summary.json`. Offline tests establish component mechanics, not real engine
capture, online Grok behavior, provider entitlement or trading profitability.
