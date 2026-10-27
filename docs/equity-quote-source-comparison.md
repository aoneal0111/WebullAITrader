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
