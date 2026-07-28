from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from app.analytics import AnalyticsController, AnalyticsSnapshot


class QtAnalyticsBridge(QObject):
    analytics_changed = Signal(object)

    def __init__(
        self,
        controller: AnalyticsController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(controller, AnalyticsController):
            raise TypeError("controller must be AnalyticsController")
        self._controller = controller
        self._listener_id = controller.subscribe(self._forward)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._controller.unsubscribe(self._listener_id)
        self._closed = True

    def _forward(self, snapshot: AnalyticsSnapshot) -> None:
        self.analytics_changed.emit(snapshot)
