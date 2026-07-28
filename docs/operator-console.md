# Atlas v1.0.1 — Operator Console

The Atlas desktop shell opens at a safe, readable size (approximately 90% of
the available desktop, with a 1280×720 minimum) and restores geometry when it
is still on-screen. The left navigation keeps a stable width while the chart
and operation panels receive the remaining space.

The Dashboard is a live paper-trading view. Its banner is driven by the shared
`ApplicationStateStore` runtime phase: `STOPPED`, `STARTING`, `RUNNING`,
`STOPPING`, or `FAILED`. Start and stop requests are idempotent and the action
button is disabled during transitions. Heartbeats are the runtime lifecycle
timestamps projected from `RuntimeCycleCompleted` events.

The chart is a lightweight Qt canvas fed by `CandleSeriesModel`. It aggregates
normalized trade updates into bounded 1m, 5m, or 15m OHLCV buckets. No broker or
runtime service is constructed by the GUI. Trade markers are intended for
execution, order, lifecycle, and decision events; missing values remain
unavailable rather than being fabricated.

Historical analytics, experiments/playback, Event Store browsing, and runtime
diagnostics have dedicated navigation destinations. They continue to use the
composition-owned controllers and read models. A future historical chart
adapter can implement the same candle/marker presentation models without
duplicating the runtime or replay engine.

Known limitation: the current release renders an empty chart until a live
candle adapter supplies normalized market updates; existing market-data
contracts are intentionally unchanged.
