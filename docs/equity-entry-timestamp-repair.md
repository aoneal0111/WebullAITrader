# Queued quote invalidation of a newer PAPER entry

An entry can be accepted while a quote is waiting in the authoritative queue.
The quote can arrive promptly yet still have an exchange timestamp earlier
than the order's latest persisted transition. If that quote's ask is at or
below the entry's structural stop, `_invalidate_entry_orders` previously
attempted cancellation at the quote timestamp. The order state machine
correctly raised `OrderValidationError: transition time cannot precede
updated_at`. That exception propagated out of gateway processing and stopped
the authoritative lane before matching could run.

Structural invalidation now skips only orders updated after the quote. The
existing matching chronology check likewise prevents that quote filling those
orders. The same quote can still invalidate chronologically eligible older
entries. A later current breach still cancels; a later valid executable quote
can fill. No provider timestamp is advanced, transition rule weakened, quote
age tolerance widened, or structural stop removed.

Regression tests cover persistent recovery after a subsequent valid fill,
current and equal-time structural cancellations, mixed-age orders on one
symbol, and an authoritative lane continuing to another symbol. The same
stale-breach sequence raises the state-machine exception on the prior code.
Private captured order/quote evidence is retained outside the repository.

`tools/repair-equity-entry-timestamps.ps1` validates the update in a detached
checkout before a backed-up fast-forward. It reuses the operator's existing
trial and launchers without resetting balances, expiry or request budgets.
Crypto's separately prepared worker/source/configuration are unaffected. No
runtime or network-capable trading process is started during installation.
