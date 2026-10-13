# Near-due history request timer

The operator's post-alignment capture showed six rejected windows per coin.
Each had fresh quotes and no bar gaps, but latest bar ages around 71.7 seconds.
Latest bar observation times came from the preceding minute's request cycle.
Polling phase alignment alone did not solve this interaction.

Discovery runs before history scheduling and its request duration varies. If one
cycle's discovery finishes 20 milliseconds sooner than the preceding cycle,
history's independent 60-second request timer sees only 59.98 seconds elapsed.
It skips history even though the poller's refresh starts are 60 seconds apart.

The default background one-minute poller now permits an interruptible wait of at
most five seconds for a nearly-due default M1/M5 history timer. It then reads the
actual clock again and applies the original due test. History requests never run
early; cutoffs are never backdated. The following quote is requested only after
the normally due history acquisition and capture. Interrupting the wait through
shutdown ends that refresh without further history or snapshot requests.

There is no retry or additional history request. The existing per-cycle budget,
60-second request minimum and quote/history freshness guards remain unchanged.
Timers more than five seconds away are not waited for. Custom poll cadences and
custom history cadences retain their former behavior. Direct diagnostic
`refresh_once()` calls remain non-waiting unless explicitly opted in.

Offline tests reproduce the missed cycle using variable discovery duration,
verify requests remain exactly 60 seconds apart after the short wait, and cover
shutdown, custom timing and the bounded wait. The preceding poll-phase tests did
not model this independent timer interaction; they remain useful for phase drift
but are not proof of real provider readiness. A new normal PAPER capture after
installation is still needed. Saved records remain unchanged, and neither this
change nor its tests establish strategy profitability.

## Timer completion recheck

The next operator capture improved to 20 of 21 observations reaching the worker
per coin, but one window still had previous-cycle bars about 71.89 seconds old.
Those timestamps alone do not distinguish a skipped request from a request,
normalization or capture failure. They do not establish an early timer wakeup.

An offline regression does demonstrate that a one-millisecond early timeout or
clock reading leaves the previous single-wait code short of the request minimum,
so it skips history. The wait now rechecks the actual due time. A monotonic
five-second total wait budget and eight-attempt bound prevent loops on a stalled
or rewound wall clock. Sub-millisecond remainders wait at least one millisecond
within the remaining budget. Shutdown interrupts each wait. No provider request
retry is added; history is requested only if the original clock-based due guard
passes. This repairs a reproduced scheduling defect, while attribution of the
remaining real observation still requires request/capture evidence.
