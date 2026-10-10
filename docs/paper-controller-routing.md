# Durable PAPER equity routing and recovery

`PaperControllerRouter` connects the admission ledger to a real
`PaperOrderGateway`. It is an explicit adapter, not installed in desktop
composition. Existing Atlas entry and protection behavior remains active.

The adapter supports Warrior and Quick Scalper long, whole-share stock LIMIT
entries only. Crypto, options, futures, short entries and contract multipliers
are rejected rather than sent through the stock matcher.

Construction requires a durable gateway with the matching account identity.
Use one trusted router ingress per account and campaign. Do not rotate campaigns
while reservations remain active. Direct writes to the order book are not a
supported execution path. Separate processes must send commands to this one
router process rather than each constructing a gateway over the same database.

Before reservation and again before claiming dispatch, a supplied deterministic
`authorize` callback must return exactly `True`. The composition owner must
supply existing session/halt, executable quote, risk and protection-readiness
checks; a model response is not authorization. The callback must not mutate
execution state. Admission itself validates the quote timestamp and expiry.

Orders carry a stable hashed client ID, command ID, policy version, lifecycle,
structural stop and absolute entry deadline. Dispatch happens outside the
controller database transaction. Unknown broker exceptions or rejection without
stored order evidence leave the reservation held. Recovery searches recorded
orders, never resends an uncertain command.

Reconciliation holds the gateway mutation lock while reading complete current
book history and updating controller bookkeeping. It checks entry identity and
broker ID, recovers a missed acknowledgement, sums filled shares by lifecycle
including cancelled partial orders, and releases capital only when the net is
zero and every lifecycle order is terminal. It does not infer ownership from
aggregate symbol positions. Missing/conflicting evidence retains the budget.

Any unmatched open order or unmatched nonzero filled inventory blocks routing.
Existing desktop exposure must be adopted in a separate migration stage; an
empty controller ledger cannot bypass it. Closing an unfilled expired or
cancelled entry permits release, but its command identity remains terminal.
Lifecycle identities must not be reused for new entries.

This stage does not install automatic stops, replace existing exit management,
route live Webull orders, launch workers or enable AI. Before desktop activation,
wire the trusted authorization/protection path and verify complete campaign
adoption. Protective exits continue through the existing independent gateway
path and must never wait for AI or new-entry budget availability.

Validation covers actual gateway placement, durable restart, lost acknowledgement,
unknown submission, identity conflicts, cancelled partial fills, outstanding exits,
unmanaged exposure and unsupported asset/account rejection.
