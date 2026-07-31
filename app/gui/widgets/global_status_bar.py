from __future__ import annotations

from PySide6.QtCore import QDateTime, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from app.gui.models import (
    DashboardSnapshot,
    HealthDashboardSnapshot,
)
from app.gui.widgets.common import StatusIndicator


class GlobalStatusBar(QWidget):
    """Render immutable application and infrastructure status summaries."""

    def __init__(self, *, version: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(10)
        self.runtime = StatusIndicator("Runtime Unknown")
        self.data_feed = StatusIndicator("Feed Unknown")
        self.broker = StatusIndicator("Broker Unknown")
        self.ai = StatusIndicator("AI Unknown")
        indicators = (
            self.runtime,
            self.data_feed,
            self.broker,
            self.ai,
        )
        for index, indicator in enumerate(indicators):
            layout.addWidget(indicator)
            if index < len(indicators) - 1:
                separator = QFrame()
                separator.setObjectName("statusSeparator")
                separator.setFrameShape(QFrame.Shape.VLine)
                layout.addWidget(separator)
        layout.addStretch()
        self.version = QLabel(f"Atlas v{version}")
        self.version.setObjectName("muted")
        layout.addWidget(self.version)
        self.local_time = QLabel()
        self.local_time.setObjectName("muted")
        self.local_time.setToolTip("Current local time")
        layout.addWidget(self.local_time)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_time)
        self._clock.start(1000)
        self._update_time()

    def _update_time(self) -> None:
        self.local_time.setText(
            QDateTime.currentDateTime().toString("yyyy-MM-dd  hh:mm:ss AP")
        )

    def render_dashboard(self, snapshot: DashboardSnapshot) -> None:
        runtime = snapshot.runtime
        self.runtime.set_status(
            f"Runtime {runtime.state.value.title()}",
            "good" if runtime.state.value == "RUNNING" else "neutral",
        )

    def render_health(self, snapshot: HealthDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
        feed = metrics.get("Market Data", "--")
        broker = metrics.get("Broker", "--")
        ai = metrics.get("AI", "--")
        feed = "UNKNOWN" if feed == "--" else feed
        broker = "UNKNOWN" if broker == "--" else broker
        ai = "UNKNOWN" if ai == "--" else ai
        self.data_feed.set_status(
            f"Feed {feed.title()}",
            _level(feed),
        )
        self.broker.set_status(
            f"Broker {broker.title()}",
            _level(broker),
        )
        self.ai.set_status(
            f"AI {ai.title()}",
            _level(ai),
        )


def _level(value: str) -> str:
    normalized = value.upper()
    if normalized in {"CONNECTED", "READY", "RUNNING", "HEALTHY"}:
        return "good"
    if normalized in {"FAILED", "ERROR", "DISCONNECTED", "UNAVAILABLE"}:
        return "danger"
    if normalized in {"DEGRADED", "STARTING", "RECONNECTING"}:
        return "warn"
    return "neutral"


__all__ = ["GlobalStatusBar"]
