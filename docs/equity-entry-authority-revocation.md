# Entry authority after terminal failure and Stop

A terminal market-data failure revoked Warrior's shared entry permission, but a
later or already-running observational callback could restore it: routine
health recovery checked the capture writer, not the terminal stream state. Stop
also revoked the same boolean before waiting for processing to finish, allowing
an in-flight callback's final health check to overwrite that revocation.

The desktop sidecar now retains a session-level entry revocation reason.
Terminal failure and Stop set it before notifying optional Grok entry components.
Routine health checks keep entry permission false and show degraded strategy
health while it remains set. A terminal failure arriving during startup also
survives the final STARTED transition. Notification failures cannot prevent the
revocation or unwind the market-data failure handler.

Calling Start directly on the revoked/degraded session does not recreate its
resources or clear the reason. A completed Stop followed by an explicit Start
opens a new session and can restore normal entry permission. A Stop that times
out does not authorize a restart over the still-active resources. Writer-only
health recovery remains unchanged when no terminal revocation occurred.

Warrior and the Quick Scalper stream observer share this entry permission.
Observation and authoritative protection reconciliation remain reachable while
entries are revoked. The patch does not cancel existing orders, submit exits,
change stop prices, or invent fills during an outage. Existing bridge ownership,
quote freshness and quantity checks still apply. It does not revoke a broker
submission already authorized before the failure, or prove that an unavailable
feed has recovered when an operator explicitly starts a new session.

The supplied FEAM/FSLY captures have a large October 9–13 gap, but cannot establish
which runtime or feed state caused it. These regressions reproduce a current
entry-authority defect; they do not establish the cause of those historical
losses. PAPER matching still requires the runtime and feed to run. No protection
continues after the application exits, and no always-on supervisor is added.

Verification covers terminal-failure callbacks, an in-flight evaluation, a
deterministically paused Stop, startup failure, optional-adviser notification
errors, protection reachability, and explicit Stop/Start recovery. This is
execution-safety verification, not validation of a win-rate or profit target.
