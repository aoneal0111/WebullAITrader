from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.gui.models import HealthDashboardSnapshot, PortfolioDashboardSnapshot, ReplayWorkspaceSnapshot, RuntimeSnapshot, WatchlistSnapshot
from app.gui.models.runtime import RuntimeState


class RuntimeControlHeader(QFrame):
    """Compact command and authoritative status surface for the workstation."""

    reset_layout_requested = Signal()
    inspector_requested = Signal(bool)
    settings_requested = Signal()
    menu_requested = Signal()

    def __init__(self, runtime_controls: QWidget | None = None) -> None:
        super().__init__()
        if runtime_controls is None:
            from app.gui.widgets.workstation_panels import RuntimeControlsPanel

            runtime_controls = RuntimeControlsPanel()
        self.setObjectName("workstationHeader")
        self.setMinimumHeight(102)
        self.setMaximumHeight(102)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(8)

        identity_widget = QWidget()
        identity_widget.setObjectName("workstationIdentity")
        identity = QVBoxLayout(identity_widget)
        identity.setContentsMargins(0, 4, 0, 4)
        identity.setSpacing(2)
        identity.addStretch(1)
        title = QLabel("ATLAS X")
        title.setObjectName("missionTitle")
        subtitle = QLabel("AUTONOMOUS TRADING WORKSTATION")
        subtitle.setObjectName("identitySubtitle")
        identity.addWidget(title)
        identity.addWidget(subtitle)
        identity.addStretch(1)
        layout.addWidget(identity_widget, 25)

        controls_card = QFrame()
        controls_card.setObjectName("headerCommandCard")
        controls_layout = QVBoxLayout(controls_card)
        controls_layout.setContentsMargins(10, 6, 10, 5)
        controls_layout.setSpacing(3)
        controls_title = QLabel("RUNTIME CONTROLS")
        controls_title.setObjectName("sectionTitle")
        controls_layout.addWidget(controls_title)
        controls_layout.addWidget(runtime_controls, 1)
        self.runtime_controls = runtime_controls
        layout.addWidget(controls_card, 73)

        status_card = QFrame()
        status_card.setObjectName("headerStatusCard")
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(12, 7, 8, 7)
        status_layout.setSpacing(6)
        self._metrics: dict[str, QLabel] = {}
        for name in ("Runtime", "Market Data", "Broker", "Scanner", "Risk", "Equity", "Buying Power", "Local Time"):
            group = QVBoxLayout()
            group.setSpacing(3)
            label = QLabel(name.upper())
            label.setObjectName("metricTitle")
            value = QLabel("--")
            value.setObjectName("headerMetricValue")
            group.addWidget(label)
            group.addWidget(value)
            status_layout.addLayout(group, 1)
            self._metrics[name] = value

        for name in ("Mode", "System Health", "Session", "Daily PnL", "Candidates", "Account", "Duration"):
            self._metrics[name] = QLabel("--", self)
            self._metrics[name].hide()

        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.menu_button = QPushButton("Menu")
        self.menu_button.setObjectName("secondaryButton")
        self.menu_button.clicked.connect(self.menu_requested.emit)
        status_layout.addWidget(self.settings_button)
        status_layout.addWidget(self.menu_button)
        layout.addWidget(status_card, 102)

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
        level = {RuntimeState.RUNNING: "good", RuntimeState.STARTING: "warn", RuntimeState.STOPPING: "warn", RuntimeState.FAILED: "danger", RuntimeState.STOPPED: "danger"}.get(snapshot.state, "neutral")
        self._set("Runtime", snapshot.state.value.title(), level)
        feed = snapshot.market_feed_status or "Unknown"
        broker = snapshot.broker_status or "Unknown"
        self._set("Market Data", feed, _status_level(feed))
        self._set("Broker", broker, _status_level(broker))
        mode = snapshot.environment.upper() or "UNKNOWN"
        mode_level = "danger" if mode in {"LIVE", "PRODUCTION"} else "warn" if mode == "PAPER" else "good" if mode == "TEST" else "neutral"
        self._set("Mode", mode, mode_level)
        self._set("Risk", mode, mode_level)
        if hasattr(self.runtime_controls, "set_runtime_status"):
            self.runtime_controls.set_runtime_status(mode, snapshot.state.value)

    def render_portfolio(self, snapshot: PortfolioDashboardSnapshot) -> None:
        values = dict(snapshot.metrics)
        self._set("Equity", values.get("Equity", "--"))
        self._set("Buying Power", values.get("Buying Power", "--"))

    def render_health(self, snapshot: HealthDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
        feed = metrics.get("Market Data", "Unknown")
        broker = metrics.get("Broker", "Unknown")
        scanner = metrics.get("Scanner", "Idle")
        self._set("Market Data", feed, _status_level(feed))
        self._set("Broker", broker, _status_level(broker))
        self._set(
            "Scanner", scanner,
            "info" if scanner.upper() in {"IDLE", "UNKNOWN"} else _status_level(scanner),
        )
        status = snapshot.overall_status or "Unknown"
        level = "neutral" if status.upper() == "UNKNOWN" else snapshot.status_level or "neutral"
        self._set("System Health", status, level)

    def render_watchlist(self, snapshot: WatchlistSnapshot) -> None:
        self._set("Scanner", "Active" if snapshot.candidate_count else "Idle", "good" if snapshot.candidate_count else "info")

    def render_replay(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        del snapshot

    def _set(self, key: str, value: str, status: str = "neutral") -> None:
        label = self._metrics[key]
        if key in {"Runtime", "Market Data", "Broker", "Scanner", "Risk"}:
            value = value.upper() if value and value != "--" else "UNKNOWN"
        label.setText(value)
        label.setProperty("status", status)
        label.style().unpolish(label)
        label.style().polish(label)


def _status_level(value: str) -> str:
    normalized = value.upper()
    if "DISCONNECT" in normalized or normalized in {"FAILED", "ERROR"}:
        return "danger"
    if "CONNECT" in normalized or normalized in {"READY", "HEALTHY"}:
        return "good"
    if normalized in {"UNKNOWN", "UNAVAILABLE"}:
        return "warn"
    return "neutral"


__all__ = ["RuntimeControlHeader"]
