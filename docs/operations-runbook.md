# Operations runbook

Startup follows configuration, logging, durable stores, emergency stop, signing, broker connection, account query, reconciliation, state reconstruction, stream start, freshness validation, periodic reconciliation, then readiness. Shutdown disables submissions, clears readiness, stops intake, classifies dispatches, flushes telemetry, persists cursors, disconnects stream/broker, and closes stores within 30 seconds.

Emergency stop blocks submit and replace; cancel, reads, and reconciliation remain available. Never clear it while an unresolved mutation, broker-only order, position mismatch, stale market feed, database fault, or broker outage exists. Rotate credentials while stopped. Restore the last verified databases into an isolated environment, validate schemas and reconciliation, then perform an explicit rollout.
