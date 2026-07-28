# Atlas v1.0.1 — Operator Console

- Added a responsive, readable PySide6 operator shell with safe geometry
  persistence and dedicated operational/history destinations.
- Reworked the live Dashboard around paper-runtime state, health/account
  summaries, current positions/orders, and a central candlestick workspace.
- Added immutable bounded candle aggregation and chart-marker presentation
  models for future live and historical adapters.
- Preserved the existing OperationsBus, runtime service, replay, recording,
  Event Store, analytics, and experiment controller boundaries.
