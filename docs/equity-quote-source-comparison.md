# Read-only equity quote-source comparison

The current execution confirmation source uses one official Webull stock
snapshot per attempted authorization. Stream subscriptions already request
QUOTE, SNAPSHOT and TICK. A response arriving recently is not evidence that its
provider bid/ask timestamp is current. Trade and quote timestamps remain
separate; a fresh LAST does not refresh an old BID/ASK.

Before replacing the source or changing data subscriptions, the new explicit
probe compares stock snapshots with the dedicated stock quote/depth operation.
It makes exactly four method attempts: snapshot and depth=1 for SPY, then AAPL.
There are no retries, order/account calls, Grok requests, subscriptions, GUI
launches, installed source modifications, or settings changes. Requests are
spaced by one second and the official SDK client uses ten-second connection
and read timeouts with automatic retries disabled. This is a small comparison,
not a continuous freshness or rate-limit test. It does not prove coverage for
thin movers, exchange coverage/NBBO, or subscription entitlement.

The report exports operation labels, latency, safe failures, recognized schema
field names and parsed timestamp ages. It never exports raw response/error
bodies, prices, API keys, account identifiers or private performance. Timestamp
parsing is explicitly diagnostic: epoch-unit heuristics or ISO parsing are not
an execution contract. Unknown timestamp semantics must be reviewed against
provider documentation before any quote-source promotion. HTTP errors are not
interpreted as a particular subscription requirement without evidence.

The guarded PowerShell tool reads local credentials and production market-data
configuration from the installed PAPER baseline. It refuses live trading or
Grok order-review activation, runs offline fixture checks from a pinned
research worktree, performs four market-data attempts, prints the small summary
and removes only its own generated source worktree. Atlas may stay open for
this read-only probe. Its existing protection and strategy baseline are not
changed. No authenticated request is made during development.

Official sources reviewed October 27, 2026:
- https://developer.webull.com/apis/docs/reference/quotes/
- https://developer.webull.com/apis/docs/reference/snapshot/
- https://developer.webull.com/apis/docs/market-data-api/data-streaming-api/
- https://developer.webull.com/apis/docs/market-data-api/overview/
- Official installed webull-openapi-python-sdk 2.0.14 get_snapshot/get_quotes signatures.

Offline fixtures verify four attempts, no retry, redaction, bounded schema
inspection, future/old timestamp reporting, opt-in gating, response cleanup,
and rejection of HTTP error bodies. Successful response parsing alone never
sets execution_authorized=true or activates a new data source.

## Recent rejected movers

`probe-equity-quote-sources.ps1 -RecentRejections` requires a running Atlas
process but does not attach to or control it. It reads the existing local
performance checkpoint directory. Selection is explicitly checkpoint evidence,
not runtime-process attestation: the checkpoint must be CHECKPOINT rather than
FINAL, at most 120 seconds old, have a runtime start, and contain native
EXECUTION_QUOTE_REJECTED / QUOTE_REJECTED_BID_STALE records no older than five
minutes. Records with future/invalid times, BID age at or below five seconds,
or LAST-only failures are excluded. The freshest affected record per symbol
is retained and at most two symbols are selected. Only one checkpoint is used;
there is no fallback to another runtime when the newest eligible checkpoint
contains no qualifying rejection.

The reader examines at most 512 directory entries and five candidate files,
with a combined 16 MiB read bound and an 8,192-record bound. It exports only the
selected symbols, rejection times/ages and checkpoint age. No other checkpoint
fields, prices, account data, file paths or private performance are exported.
Missing or unsuitable evidence produces a zero-request report; it never falls
back to SPY/AAPL or old screenshots. Selection currently comes from the native
Warrior execution-quote funnel, not a claim that these exact symbols were the
Scalper failures. The comparison is useful for the shared confirmation source.

For each selected symbol, two rounds compare SNAPSHOT with depth=1, at most
eight method attempts with a one-second pause between attempts. Endpoint order
is reversed in the second round to reduce ordering bias. Source summaries show
observed quote_time ages and whether that timestamp advanced across the two
samples. This is still sequential sampling, not a simultaneous market replay
or proof of continuous quote coverage. There is no automatic source switch.
An explicit -Symbols parameter (at most two canonical equity symbols) is also
available; it never mixes with automatic selection. The original default SPY/
AAPL one-round control comparison remains unchanged when neither is supplied.
