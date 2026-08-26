from __future__ import annotations

from datetime import datetime
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.gui.models import HealthDashboardSnapshot, PortfolioDashboardSnapshot, ReplayWorkspaceSnapshot, RuntimeSnapshot, WatchlistSnapshot
from app.gui.models.runtime import RuntimeState


class RuntimeControlHeader(QFrame):
    """Full-width workstation status header backed by runtime projections."""
    reset_layout_requested = Signal()
    inspector_requested = Signal(bool)
    settings_requested = Signal()
    menu_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("workstationHeader")
        self.setMinimumHeight(70)
        self.setMaximumHeight(82)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 12, 8)
        layout.setSpacing(10)
        identity = QVBoxLayout()
        title = QLabel("ATLAS X")
        title.setObjectName("missionTitle")
        subtitle = QLabel("AUTONOMOUS TRADING WORKSTATION")
        subtitle.setObjectName("muted")
        identity.addWidget(title)
        identity.addWidget(subtitle)
        layout.addLayout(identity, 2)
        self._metrics: dict[str, QLabel] = {}
        for name in ("Runtime", "Market Data", "Broker", "Scanner", "Risk", "Mode", "Equity", "Buying Power", "Local Time"):
            group = QVBoxLayout()
            label = QLabel(name.upper())
            label.setObjectName("metricTitle")
            value = QLabel("--")
            value.setObjectName("headerMetricValue")
            group.addWidget(label)
            group.addWidget(value)
            layout.addLayout(group, 1)
            self._metrics[name] = value
        for name in ("System Health", "Session", "Daily PnL", "Candidates", "Account", "Duration"):
            self._metrics[name] = QLabel("--", self)
            self._metrics[name].hide()
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.menu_button = QPushButton("Menu")
        self.menu_button.setObjectName("secondaryButton")
        self.menu_button.clicked.connect(self.menu_requested.emit)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.menu_button)
        # Compatibility controls remain available to existing presenters, but
        # the visible operator controls live in RuntimeControlsPanel.
        self.resume_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Stop")
        self.flatten_button = QPushButton("Flatten")
        self.flatten_button.setEnabled(False)
        self.inspector_button = QPushButton("Inspector")
        for button in (self.resume_button, self.pause_button, self.stop_button, self.flatten_button, self.inspector_button):
            button.hide()
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_clock)
        self._clock.start(1000)
        self._update_clock()

    def _update_clock(self) -> None:
        self._metrics["Local Time"].setText(datetime.now().strftime("%H:%M:%S ET"))

    def render(self, snapshot: RuntimeSnapshot) -> None:
        level = {RuntimeState.RUNNING: "good", RuntimeState.STARTING: "warn", RuntimeState.STOPPING: "warn", RuntimeState.FAILED: "danger", RuntimeState.STOPPED: "neutral"}.get(snapshot.state, "neutral")
        self._set("Runtime", snapshot.state.value.title(), level)
        self._set("Market Data", snapshot.market_feed_status or "Unknown", "good" if "CONNECT" in snapshot.market_feed_status.upper() else "neutral")
        self._set("Broker", snapshot.broker_status or "Unknown", "good" if "CONNECT" in snapshot.broker_status.upper() else "neutral")
        mode = snapshot.environment.upper() or "UNKNOWN"
        self._set("Mode", mode, "danger" if mode in {"LIVE", "PRODUCTION"} else "warn" if mode == "PAPER" else "good" if mode == "TEST" else "neutral")
        self.resume_button.setText("Running" if snapshot.state is RuntimeState.RUNNING else "Start")

    def render_portfolio(self, snapshot: PortfolioDashboardSnapshot) -> None:
        values = dict(snapshot.metrics)
        self._set("Equity", values.get("Equity", "--"))
        self._set("Buying Power", values.get("Buying Power", "--"))

    def render_health(self, snapshot: HealthDashboardSnapshot) -> None:
        self._set("Market Data", "Unknown")
        self._set("Broker", "Unknown")
        self._set("Scanner", "Unknown")
        status = snapshot.overall_status or "Unknown"
        level = "neutral" if status.upper() == "UNKNOWN" else snapshot.status_level or "neutral"
        self._set("Risk", status, level)
        self._set("System Health", status, level)

    def render_watchlist(self, snapshot: WatchlistSnapshot) -> None:
        self._set("Scanner", "Active" if snapshot.candidate_count else "Idle", "good" if snapshot.candidate_count else "neutral")

    def render_replay(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        return

    def _set(self, key: str, value: str, status: str = "neutral") -> None:
        label = self._metrics[key]
        if key in {"Runtime", "Market Data", "Broker", "Scanner", "Risk"}:
            value = f"● {value.upper()}" if value and value != "--" else "● UNKNOWN"
        label.setText(value)
        label.setProperty("status", status)
        label.style().unpolish(label)
        label.style().polish(label)


__all__ = ["RuntimeControlHeader"]
