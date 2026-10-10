# Isolated engine observation workers

`app.asset_modules.engine_worker` provides a separate spawned Python process
for each catalog engine: Warrior, Scalper, Crypto, Options and Futures. The
transport is observation-only. It does not instantiate the trading strategies,
start Atlas, connect Webull, call an AI provider, submit orders or rewrite code.
Desktop composition does not activate these workers yet. Current trading and
GUI behavior remain unchanged.

The parent owns one worker handle on one thread. Immutable engine, session and
policy identities accompany every message, with a strictly increasing sequence.
The child accepts only PING, OBSERVE and STOP. OBSERVE acknowledges a named
instrument's decimal bid/ask and counts samples in a bounded 128-instrument
cache; it does not calculate entry signals or establish quote freshness.
Message size is capped at 8 KiB, prices must be finite and positive, crossed
quotes are refused, and only one request may be in flight. Requests are never
queued or retried. This intentionally provides backpressure rather than building
a market-event backlog; a later feed adapter must define coalescing and capture
rules without dropping authoritative fills/protection events.

Call `poll()` frequently to enforce response deadlines and detect child failure.
Timeouts, foreign identities, malformed responses and unexpected exit fail the
handle. No automatic restart/replay occurs. A new handle creates a new session;
future execution integration must reconcile any prior commands with the durable
controller before considering a new entry. No worker failure affects the
existing broker execution or position-protection service.

`close()` shuts down only the child owned by that handle. It joins briefly,
then terminates/kills that child if necessary; cleanup can block for up to roughly
2.2 seconds. The parent polling loop must live in a dedicated coordinator thread
when integrated into the GUI. Process isolation does not by itself guarantee
lower latency, prevent inherited environment access, or sandbox generated code.
This module does not execute user/model-supplied code.

## Offline Windows check

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/asset_modules/test_engine_worker.py -q
if ($LASTEXITCODE -ne 0) { throw "Worker tests failed." }

& .\.venv\Scripts\python.exe -m app.asset_modules.worker_check
if ($LASTEXITCODE -ne 0) { throw "Worker health check failed." }
```

The health check starts five observation processes, checks unique process IDs
and PONG responses, then closes all five before returning. It can run while Atlas
is stopped and needs no market session, broker key or AI subscription.

The next integrations are typed market/strategy snapshots with provider timing,
per-engine strategy adapters, controller-only proposal admission and independent
GUI health views. AI supervision belongs outside market matching/protection;
policy proposals require offline tests, replay and paper comparison. Existing
positions must retain their policy version. Options and futures additionally
require actual contract identity, quote, multiplier and execution/margin adapters.
