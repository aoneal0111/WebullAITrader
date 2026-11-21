# SDK shutdown and retry cancellation

An active official Webull SDK 2.0.14 worker delegates its network loop to Paho.
Paho loop_stop sets a termination flag and joins the worker. A nonzero return
from that network loop can enter QuotesClient's retry handling, including its
10-second sleep, even during deliberate shutdown. Atlas previously called
loop_stop before disconnect, so disconnect could remain unsent while its owner
waited beyond the transport's five-second deadline. Reordering alone does not
eliminate the race between termination and MQTT disconnect processing.

Atlas now requests disconnect before joining both during explicit disconnect
and when retiring an SDK client for replacement. The production SDK factory
wraps only that instance's quote retry policy: normal decisions and delay
values delegate unchanged, while the delay is spent in an interruptible Event
wait. The SDK's subsequent sleep receives zero. An owned cancellation hook sets
the same termination flag as Paho loop_stop before waking the wait, preventing
another connection attempt. It does not modify global SDK/time functions or
HTTP retries. Fresh clients receive fresh cancellation state.

Callback ingestion and registration/subscription readiness still close during
shutdown. Genuine disconnect failures remain errors; loop stop is attempted
even if disconnect raises. The transport's existing deadline remains in force.
Cancellation does not forcibly terminate an in-progress blocking SDK HTTP or
socket call; such a call can still produce a genuine pending shutdown.

Offline regressions exercise the installed SDK and Paho with local socket pairs,
not provider connections, and cancel an actual SDK retry wait. They also check
retirement ordering, error propagation and unchanged normal retry decisions and
delays. The read-only connection review now includes disconnect duration and
post-disconnect worker/connection state separately from the receive snapshot.

With Atlas closed, repair-sdk-shutdown.ps1 validates the existing continuous
PAPER spec, runs recovery/shutdown regressions in an isolated checkout, creates
a backup branch and fast-forwards only. It does not launch Atlas, trade, change
strategy parameters/settings/ledgers or restart crypto. Its optional explicit
RunConnectionCheck switch connects market data and subscribes one symbol only,
then writes a compact report. External workstation shutdown verification remains
necessary after offline validation.
