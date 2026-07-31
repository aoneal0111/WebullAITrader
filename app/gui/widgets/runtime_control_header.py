from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.gui.design.tokens import Dimensions
from app.gui.models import (
    HealthDashboardSnapshot,
    PortfolioDashboardSnapshot,
    ReplayWorkspaceSnapshot,
    RuntimeSnapshot,
)
from app.gui.models.runtime import RuntimeState
from app.gui.widgets.common import StatusIndicator


class RuntimeControlHeader(QFrame):
    """Render runtime context and expose presentation-only command buttons."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self.setMinimumHeight(Dimensions.HEADER_HEIGHT)
        self.setMaximumHeight(Dimensions.HEADER_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(10)

        identity = _metric_group()
        eyebrow = QLabel("ATLAS RUNTIME")
        eyebrow.setObjectName("eyebrow")
        self.runtime_indicator = StatusIndicator("Stopped")
        identity.addWidget(eyebrow)
        identity.addWidget(self.runtime_indicator)
        layout.addLayout(identity)

        self._metrics: dict[str, QLabel] = {}
        for label, emphasis in (
            ("Daily PnL", "primary"),
            ("System Health", "primary"),
            ("Mode", "standard"),
            ("Account", "standard"),
            ("Duration", "standard"),
        ):
            group = _metric_group()
            title = QLabel(label.upper())
            title.setObjectName("metricTitle")
            value = QLabel("--")
            value.setObjectName("monoValue")
            value.setProperty("emphasis", emphasis)
            group.addWidget(title)
            group.addWidget(value)
            layout.addLayout(group)
            self._metrics[label] = value

        layout.addStretch()
        self.resume_button = QPushButton("Start")
        self.resume_button.setObjectName("primaryButton")
        self.resume_button.setToolTip("Start runtime")
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("secondaryButton")
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("Pause or resume replay")
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        self.flatten_button = QPushButton("Flatten")
        self.flatten_button.setObjectName("dangerButton")
        self.flatten_button.setEnabled(False)
        self.flatten_button.setToolTip(
            "No flatten command boundary is configured."
        )
        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(6)
        actions.setVerticalSpacing(4)
        actions.addWidget(self.resume_button, 0, 0)
        actions.addWidget(self.pause_button, 0, 1)
        actions.addWidget(self.stop_button, 1, 0)
        actions.addWidget(self.flatten_button, 1, 1)
        layout.addLayout(actions)

    def render(self, snapshot: RuntimeSnapshot) -> None:
        levels = {
            RuntimeState.RUNNING: "good",
            RuntimeState.FAILED: "danger",
            RuntimeState.STARTING: "warn",
            RuntimeState.STOPPING: "warn",
            RuntimeState.STOPPED: "neutral",
        }
        self.runtime_indicator.set_status(
            snapshot.state.value.title(),
            levels[snapshot.state],
        )
        mode = snapshot.environment.upper()
        self._metrics["Mode"].setText(mode)
        self._metrics["Mode"].setProperty(
            "status",
            "danger" if mode in {"LIVE", "PRODUCTION"} else
            "warn" if mode == "PAPER" else "good" if mode == "TEST" else "neutral",
        )
        self._metrics["Mode"].style().unpolish(self._metrics["Mode"])
        self._metrics["Mode"].style().polish(self._metrics["Mode"])
        account = snapshot.account
        self._metrics["Account"].setText(
            account if len(account) <= 16 else f"{account[:6]}…{account[-6:]}"
        )
        self._metrics["Account"].setToolTip(account)
        self._metrics["Duration"].setText(snapshot.runtime_duration)
        self.resume_button.setText(
            "Running"
            if snapshot.state is RuntimeState.RUNNING
            else "Start"
        )

    def render_portfolio(
        self,
        snapshot: PortfolioDashboardSnapshot,
    ) -> None:
        values = dict(snapshot.metrics)
        value = values.get("Total P/L", "--")
        self._metrics["Daily PnL"].setText(value)
        self._set_metric_status(
            "Daily PnL",
            "good"
            if value.startswith("+")
            else "danger"
            if value.startswith("-")
            else "neutral",
        )

    def render_health(self, snapshot: HealthDashboardSnapshot) -> None:
        self._metrics["System Health"].setText(snapshot.overall_status)
        self._set_metric_status(
            "System Health",
            (
                "neutral"
                if snapshot.overall_status.upper() == "UNKNOWN"
                else snapshot.status_level
            ),
        )

    def render_replay(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        self.pause_button.setText(
            "Pause" if snapshot.can_pause else "Resume"
        )
        self.pause_button.setEnabled(
            snapshot.can_pause or snapshot.can_play
        )

    def _set_metric_status(self, metric: str, status: str) -> None:
        label = self._metrics[metric]
        label.setProperty("status", status)
        label.style().unpolish(label)
        label.style().polish(label)


def _metric_group() -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    return layout


__all__ = ["RuntimeControlHeader"]
