from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)

from app.gui.design.tokens import Dimensions
from app.gui.models import (
    HealthDashboardSnapshot,
    PortfolioDashboardSnapshot,
    ReplayWorkspaceSnapshot,
    RuntimeSnapshot,
    WatchlistSnapshot,
)
from app.gui.models.runtime import RuntimeState
from app.gui.widgets.common import StatusIndicator


class RuntimeControlHeader(QFrame):
    """Compact mission-control ribbon backed only by presenter snapshots."""

    reset_layout_requested = Signal()
    inspector_requested = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("terminalHeader")
        self.setMinimumHeight(Dimensions.HEADER_HEIGHT)
        self.setMaximumHeight(Dimensions.HEADER_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 10, 6)
        layout.setSpacing(0)

        identity = _metric_group()
        title = QLabel("Atlas Mission Control")
        title.setObjectName("missionTitle")
        self.runtime_indicator = StatusIndicator("Stopped")
        identity.addWidget(title)
        identity.addWidget(self.runtime_indicator)
        layout.addLayout(identity, 2)

        self._metrics: dict[str, QLabel] = {}
        for label in (
            "Session",
            "Runtime",
            "Daily PnL",
            "Buying Power",
            "Candidates",
            "System Health",
        ):
            layout.addWidget(_separator())
            group = _metric_group()
            title_label = QLabel(label.upper())
            title_label.setObjectName("metricTitle")
            value = QLabel("--")
            value.setObjectName("headerMetricValue")
            group.addWidget(title_label)
            group.addWidget(value)
            layout.addLayout(group, 1)
            self._metrics[label] = value

        # Compatibility facts remain available to existing presentation tests,
        # but no longer consume the primary dashboard row.
        for label in ("Mode", "Account", "Duration"):
            value = QLabel("--", self)
            value.hide()
            self._metrics[label] = value

        layout.addStretch(1)
        self.inspector_button = QPushButton("Inspector")
        self.inspector_button.setObjectName("secondaryButton")
        self.inspector_button.setCheckable(True)
        self.inspector_button.setToolTip("Show secondary Atlas intelligence")
        self.inspector_button.toggled.connect(self.inspector_requested.emit)
        layout.addWidget(self.inspector_button)
        self.resume_button = QPushButton("Start")
        self.resume_button.setObjectName("primaryButton")
        self.resume_button.setToolTip("Start runtime")
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("secondaryButton")
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("Pause or resume replay")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setToolTip("Stop runtime")
        for button in (self.resume_button, self.pause_button, self.stop_button):
            button.setMinimumWidth(96)
            layout.addWidget(button)

        self.flatten_button = QPushButton("Flatten", self)
        self.flatten_button.setMinimumWidth(110)
        self.flatten_button.setEnabled(False)
        self.flatten_button.setToolTip("No flatten command boundary is configured.")
        self.flatten_button.hide()

        self.overflow_button = QToolButton()
        self.overflow_button.setObjectName("overflowButton")
        self.overflow_button.setText("\u22ee")
        self.overflow_button.setToolTip("Mission-control menu")
        self.overflow_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.overflow_button)
        self.reset_layout_action = QAction("Reset Layout", menu)
        self.reset_layout_action.triggered.connect(self.reset_layout_requested.emit)
        menu.addAction(self.reset_layout_action)
        self.overflow_button.setMenu(menu)
        layout.addWidget(self.overflow_button)

    def render(self, snapshot: RuntimeSnapshot) -> None:
        levels = {
            RuntimeState.RUNNING: "good",
            RuntimeState.FAILED: "danger",
            RuntimeState.STARTING: "warn",
            RuntimeState.STOPPING: "warn",
            RuntimeState.STOPPED: "neutral",
        }
        state_text = snapshot.state.value.title()
        self.runtime_indicator.set_status(state_text, levels[snapshot.state])
        self._metrics["Runtime"].setText(snapshot.runtime_duration)
        self._set_metric_status("Runtime", levels[snapshot.state])

        mode = snapshot.environment.upper()
        self._metrics["Mode"].setText(mode)
        self._set_metric_status(
            "Mode",
            "danger" if mode in {"LIVE", "PRODUCTION"}
            else "warn" if mode == "PAPER"
            else "good" if mode == "TEST"
            else "neutral",
        )
        account = snapshot.account
        self._metrics["Account"].setText(
            account if len(account) <= 16 else f"{account[:6]}\u2026{account[-6:]}"
        )
        self._metrics["Account"].setToolTip(account)
        self._metrics["Duration"].setText(snapshot.runtime_duration)
        self.resume_button.setText(
            "Running" if snapshot.state is RuntimeState.RUNNING else "Start"
        )

    def render_portfolio(self, snapshot: PortfolioDashboardSnapshot) -> None:
        values = dict(snapshot.metrics)
        pnl = values.get("Total P/L", "--")
        self._metrics["Daily PnL"].setText(pnl)
        self._metrics["Buying Power"].setText(values.get("Buying Power", "--"))
        self._set_metric_status(
            "Daily PnL",
            "good" if pnl.startswith("+")
            else "danger" if pnl.startswith("-")
            else "neutral",
        )

    def render_health(self, snapshot: HealthDashboardSnapshot) -> None:
        values = dict(snapshot.metrics)
        self._metrics["Session"].setText(values.get("Current Session", "--"))
        self._metrics["System Health"].setText(snapshot.overall_status)
        self._set_metric_status(
            "System Health",
            "neutral" if snapshot.overall_status.upper() == "UNKNOWN"
            else snapshot.status_level,
        )

    def render_watchlist(self, snapshot: WatchlistSnapshot) -> None:
        self._metrics["Candidates"].setText(str(snapshot.candidate_count))
        self._set_metric_status(
            "Candidates", "good" if snapshot.candidate_count else "neutral"
        )

    def render_replay(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        self.pause_button.setText("Pause" if snapshot.can_pause else "Resume")
        self.pause_button.setEnabled(snapshot.can_pause or snapshot.can_play)

    def _set_metric_status(self, metric: str, status: str) -> None:
        label = self._metrics[metric]
        label.setProperty("status", status)
        label.style().unpolish(label)
        label.style().polish(label)


def _metric_group() -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(2)
    return layout


def _separator() -> QFrame:
    separator = QFrame()
    separator.setObjectName("headerSeparator")
    separator.setFrameShape(QFrame.Shape.VLine)
    return separator


__all__ = ["RuntimeControlHeader"]
