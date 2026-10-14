# PAPER protection feed lifecycle — offline adapter boundary

`PaperProtectionFeed` supplies a serialized connection gate around an existing
`PaperProtectionHost`. A trusted future provider adapter must route every event
through this gate. It creates no SDK client, subscription, thread, GUI or service.
Installing it does not start protection against the user's actual ledger.

Every connection receives a new process-local identity token. Callbacks from an
older connection cannot fill orders, refresh health, or disconnect the new one.
Connecting pauses all owned symbols. It does not infer tradability from quotes.
A new provider-authored RESUME, newer than the interruption barrier, is required
before a subsequent quote may be considered for matching. Existing host checks
still reject stale, future, pre-start, duplicate and out-of-order events.

Disconnect, an invalid current-generation event, a source mismatch, or feed
silence revokes the generation and pauses all symbols. Stops and pending exits
remain durable; interruption does not fabricate an exchange HALT, cancel exits,
reset inventory, or produce a fill. Reconnection needs new status again.

The caller must choose a positive silence deadline of at most five seconds and
call `check_health()` from its transport loop even without incoming messages.
The gate also checks the deadline before every current-generation callback.
There is no autonomous watchdog thread here. Monotonic-clock failures pause
matching and raise. Only accepted quotes or accepted halt/resume statuses renew
the deadline. Duplicate events, heartbeats, trades, unowned-symbol events and
quotes blocked by a halt do not renew it. A connection can therefore expire
while halted or waiting for status; that is deliberate. No expired generation
can resume simply because the next quote is fresh.

This is an application lifecycle contract, not a security boundary: trusted code
can still call `host.process` directly. The source string is checked for routing
consistency, not cryptographic provider authentication. A production adapter
must establish provenance and tradability independently, subscribe to inventory
symbols, handle status freshness and session changes, and call disconnect on
transport errors. No production caller is wired in this stage.

Source inspection found that `WebullMarketEventParser` currently emits quotes
and trades, with quote sizes, but does not map exchange halt/resume messages.
The existing instrument lookup's missing-status fallback allows tradability;
that fallback is not accepted as affirmative status evidence for this host.
An instrument-list status alone also does not establish timely halt delivery.
These existing parser and lookup behaviors are unchanged. A verified status
channel and real response evidence are still needed before an online launcher.

The temporary separate-process check now covers silence, rejected old callbacks,
fresh-status requirements after reconnect, and eventual synthetic stop closure:

```powershell
& .\.venv\Scripts\python.exe -m app.paper_trading.protection_host_check --synthetic
```

It makes zero network requests and never opens the operator's ledger. It does
not establish live feed readiness, profitable strategies, or an improved win rate.
