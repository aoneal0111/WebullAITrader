# Atlas production thread ownership

This inventory records the runtime boundary used by the desktop application.
It is intentionally about execution ownership, not strategy behavior.

| Work | Owning thread | Boundary |
|---|---|---|
| Broker connect/authentication and account REST polling | `desktop-runtime-service` | `RuntimeService` owns the driver thread; `DesktopBrokerRuntimeDriver.run` and `_poll_accounts` execute there. |
| Webull stream receive | `desktop-market-data` | `DesktopBrokerRuntimeDriver._receive_market_data` calls the transport. |
| Webull payload classification and parsing | `desktop-market-data` | `WebullWebSocketClient.receive` invokes the parser synchronously on the receive thread. |
| Scanner `run_available` and engine consumption | `desktop-market-data` | `LiveScannerCoordinator.run_available` drains the transport and calls the scanner engine on the receive thread. |
| Scanner immutable snapshot generation/publication | `desktop-market-data` | The driver obtains a snapshot after a non-empty drain. `ScannerSnapshotPublisher` change-detects before emitting projection events. |
| Runtime projection sinks | Publishing worker | `CompositeRuntimeEventSink` and all read-model projectors are synchronous. Market-data projections therefore run on `desktop-market-data`; broker REST projections run on `desktop-runtime-service`. |
| `OperationsBus` and `ApplicationStateStore` update | Publishing worker | Both are synchronous and thread-safe. Store listeners receive immutable snapshots on the publishing worker. |
| Worker-to-Qt transfer | Worker then Qt main thread | `QtStateBridge._forward_state` only replaces one pending immutable snapshot. Its 125 ms `QTimer` executes `_flush` on the Qt main thread. |
| Presenter update, Mission Control, Atlas Focus, QWidget/table mutation | Qt main thread | The coalesced `state_changed` signal calls `MainWindow._render_state`; all presenter and widget rendering remains on Qt. |
| Chart REST retrieval/projection | `atlas-chart-rest_0` | `ChartPresenter` uses one bounded executor worker. Its completion signal applies the immutable chart model on Qt. |
| Chart interaction/rendering | Qt main thread | Symbol/timeframe intent and QWidget/chart-canvas mutation remain on Qt. |
| Operational Event Store projection | Publishing worker | `TimelineProjection` filters raw market tape before its bounded immutable projection; the visible table rebuild occurs only at the coalesced Qt boundary. |
| Logging | Calling worker (synchronous handler) | Raw receive and stale diagnostics are sampled/aggregated; GUI performance diagnostics are emitted by the Qt timer every bounded interval. |
| Shutdown | Worker plus Qt polling | Qt requests cooperative stop and polls with `QTimer`; it does not join the runtime worker in `closeEvent`. Final composition cleanup occurs after the Qt loop exits. |

No broker REST call, stream receive, parser call, scanner evaluation, scanner
snapshot generation, or chart REST projection is executed by the Qt main
thread. The Qt thread only selects the latest immutable state and mutates
presentation objects.
