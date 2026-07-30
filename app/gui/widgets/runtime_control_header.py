from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)
        context = QHBoxLayout()
        context.setSpacing(18)

        identity = QVBoxLayout()
        eyebrow = QLabel("ATLAS AUTONOMOUS RUNTIME")
        eyebrow.setObjectName("eyebrow")
        self.runtime_indicator = StatusIndicator("Stopped")
        identity.addWidget(eyebrow)
        identity.addWidget(self.runtime_indicator)
        context.addLayout(identity)

        self._metrics: dict[str, QLabel] = {}
        for label in (
            "Mode",
            "Account",
            "Duration",
            "Total P&L",
            "System Health",
        ):
            group = QVBoxLayout()
            title = QLabel(label.upper())
            title.setObjectName("metricTitle")
            value = QLabel("--")
            value.setObjectName("monoValue")
            group.addWidget(title)
            group.addWidget(value)
            context.addLayout(group)
            self._metrics[label] = value

        context.addStretch()
        layout.addLayout(context)
        actions = QHBoxLayout()
        actions.addStretch()
        self.resume_button = QPushButton("Start Runtime")
        self.resume_button.setObjectName("primaryButton")
        self.pause_button = QPushButton("Pause Replay")
        self.pause_button.setObjectName("secondaryButton")
        self.pause_button.setEnabled(False)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        self.flatten_button = QPushButton("Flatten")
        self.flatten_button.setObjectName("dangerButton")
        self.flatten_button.setEnabled(False)
        self.flatten_button.setToolTip(
            "No flatten command boundary is configured."
        )
        for button in (
            self.resume_button,
            self.pause_button,
            self.stop_button,
            self.flatten_button,
        ):
            actions.addWidget(button)
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
        self._metrics["Mode"].setText(snapshot.environment)
        self._metrics["Account"].setText(snapshot.account)
        self._metrics["Duration"].setText(snapshot.runtime_duration)
        self.resume_button.setText(
            "Runtime Active"
            if snapshot.state is RuntimeState.RUNNING
            else "Start Runtime"
        )

    def render_portfolio(
        self,
        snapshot: PortfolioDashboardSnapshot,
    ) -> None:
        values = dict(snapshot.metrics)
        self._metrics["Total P&L"].setText(
            values.get("Total P/L", "--")
        )

    def render_health(self, snapshot: HealthDashboardSnapshot) -> None:
        self._metrics["System Health"].setText(snapshot.overall_status)

    def render_replay(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        self.pause_button.setText(
            "Pause Replay" if snapshot.can_pause else "Resume Replay"
        )
        self.pause_button.setEnabled(
            snapshot.can_pause or snapshot.can_play
        )


__all__ = ["RuntimeControlHeader"]
