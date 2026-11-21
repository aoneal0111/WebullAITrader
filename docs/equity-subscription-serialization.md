# Equity subscription transaction repair

The reference/universe workers and market-event consumer could concurrently
replace the same Webull streaming subscription. Each replacement unsubscribed
all active symbols before subscribing its new set. Without serialization, both
callers could enter the SDK unsubscribe endpoint against the same active session.

The coordinator now serializes selection, transport update and publication of
its channel tuple. Identical pending updates coalesce after the first succeeds.
The SDK backend independently serializes connect, disconnect and complete
unsubscribe/subscribe transactions, including duplicate detection. Receive
callbacks retain their existing queue path and do not take this transaction lock.

Subscription acknowledgement is cleared before unsubscribe. On an unsubscribe
exception the previous tuple is retained as cleanup-required state, not reported
as acknowledged, so a later retry cannot silently leak the old server set. A
successful subscribe response is acknowledged only while the connection and
registration are still valid. Persistent SDK/authentication/business failures
remain failures; no blanket exception suppression, fabricated ticks, altered
provider timestamps or relaxed entry checks are introduced.

UNSUBSCRIBE_FAILED and SUBSCRIBE_FAILED diagnostics record bounded exception
class, SDK business code, HTTP status where available, and operation duration.
They do not record raw SDK error bodies or messages. Direct consumer exceptions
also record CONSUMER_FAILED with the pipeline stage and safe SDK identity; stream
terminal counters now cover direct subscription failures as well as receive
failures. Authoritative processing errors remain separately classified.

Three regression tests fail on the previous implementation: overlapping identical
replacements, acknowledgement retained after failed unsubscribe, and false
acknowledgement after disconnection during subscribe. They pass after repair.
Additional cases cover lifecycle serialization, coordinator coalescing, existing
subscription limits/retained symbols, and safe error diagnostics.

The stopped-only installer retains the existing manual launcher, trial expiry,
paid budget, ledgers and environment file. Crypto is not restarted. It starts no
Atlas process and makes no provider requests. This repairs the reproduced overlap
and acknowledgement defects; it does not verify external Webull connectivity or
establish that separate historical INVALID_SYMBOL responses caused a shutdown.
