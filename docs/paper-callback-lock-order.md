# PAPER callback lock order

PAPER mutations previously invoked projection and strategy callbacks while holding
the gateway lock. A matching thread could hold that lock and wait for a strategy
lock, while the strategy thread held its own lock and submitted or cancelled an
order through the gateway. Neither thread could advance.

The gateway now persists and updates canonical state under its mutation lock,
queues the resulting events, and releases the lock before invoking observers.
One dispatcher delivers the queue in FIFO order. A concurrent or reentrant
mutation commits and returns without waiting for the active dispatcher; that
dispatcher subsequently delivers its events. Waiting for the dispatcher would
reintroduce the same lock cycle.

In an uncontended call, callbacks still finish before the command returns. During
concurrent delivery, a command acknowledgement confirms canonical state, while
the projection and strategy callbacks may still be pending. Events from nested
submissions cannot overtake the remainder of the current committed batch.
The pending queue is lossless and has no hard capacity; a permanently blocked
external observer still requires investigation. This change removes the gateway
mutation-lock dependency, rather than adding a timeout or dropping fill events.

`paper.runtime_event_delivery` measures observer time outside the mutation lock.
`paper.fill_event_emission` now measures queueing time. A timing sample is recorded
after its call returns, so a blocked callback has no completed duration sample.

Regression coverage exercises a fill callback competing with a strategy-held
lock and a gateway cancellation, FIFO delivery during nested submissions, and
dispatcher recovery after an observer exception. Existing feedback tests cover
partial fills, protective orders, target closure, cash and position projections,
and ownership cleanup. A bounded timeout in the regression prevents the old
deadlock from hanging the test process; production lock acquisition has no new
timeout.
