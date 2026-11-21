# PAPER equity management repair

The repair addresses four independent boundaries. It does not qualify a strategy
as profitable or turn an AI PASS decision into an entry.

## Filled inventory and protection

For native Scalper lifecycles, SELL authorization and bracket resizing now use
canonical filled orders for the exact lifecycle rather than a potentially delayed
presentation position quantity. A unique positive contributor, declared Scalper
ownership, and absence of negative contributing lots are required. Ambiguity
fails closed. Historical flat lots are excluded. Other strategy paths retain
their existing quantity source.

The same quantity is used at the runtime, bridge, correlated target/stop resize,
and gateway boundaries. A partial fill is protected before the remaining entry
fills; its stop grows only with proven filled quantity. Full exits retain their
quantity and cancel competing protection correctly. The regression uses an
18-share fill followed by 57 shares while the presentation quantity stays zero,
and verifies a maximum-hold exit of exactly 75 shares. It fails on the prior
commit and passes with the repair.

The offline recovery checker verifies a closed review's file hashes and restores
its ledger to a private new copy. It simulates protection submissions there,
without quotes, provider calls, AI calls, BUYs or production order requests. It
reports the copy's protected quantity separately. It does not protect the actual
workstation position, which still requires normal manual runtime recovery.

Expired valid experiment files can be loaded for management startup without
renewing expiry or budget. `active()` remains strict, so new entries remain
blocked. Future or structurally invalid files still fail.

## Quote and controller consistency

The explicit account PAPER clock trial now carries the same 250 ms future bound
through both final controller authorization and durable admission. Old profiles
and Warrior retain their original timing rules. Existing maximum book ages are
unchanged.

In that explicit trial, native Scalper confirmations can use already subscribed,
normalized Webull QUOTE and TRADE events. Both provider book time and local arrival
age must be current. Missing trade/book evidence, wrong providers, invalid prices,
large future times, delayed callbacks, stale books and out-of-order updates cannot
be used to fabricate freshness. Stale/missing stream data falls back to the
existing single REST request, whose policy checks still apply. A stream decision
is never written as provenance for an unrelated REST response. No subscriptions
or provider timestamps are changed. Warrior and crypto source paths are unchanged.

## Failure reporting and Grok rate limits

Readiness now includes exact bridge/controller rejection counters, exit failure
counters, proven open quantity, working stop quantity and quote-source counts.
Safe failure categories are also recorded in the journal. This prevents an exit
intent from being mistaken for an accepted protective or liquidation order.

HTTP 429 honors numeric or date Retry-After with a one-hour maximum and a default
one-minute delay for invalid/missing headers. The cooldown is shared across roles
of that client, checked before durable attempt reservation, and causes no automatic
retry. Reports identify RATE_LIMITED and a bounded delay without retaining error
bodies, credentials or raw headers. Provider availability cannot be guaranteed.

## Installation

`repair-equity-management.ps1` runs tests and the private-copy recovery check in
an isolated checkout before a backed-up fast-forward. It prepares a fresh manual
clock-policy trial whose eight-hour clock starts at manual launch. It starts
nothing, leaves crypto containers alone, and retains existing trial files,
ledgers and budgets.
The tests use synthetic inputs; operational results are not published with code.
