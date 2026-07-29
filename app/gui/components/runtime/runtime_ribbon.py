from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from app.gui.components.buttons import IconButton
from app.gui.components.cards import MetricCard
from app.gui.components.common import SectionTitle, StatusPill
from app.gui.components.runtime.runtime_badge import RuntimeBadge
from app.gui.models import RuntimeSnapshot
from app.gui.theme import Icons, Spacing


class RuntimeRibbon(QFrame):
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("runtimeRibbon")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            Spacing.LG,
            Spacing.MD,
            Spacing.LG,
            Spacing.MD,
        )
        layout.setSpacing(Spacing.LG)
        layout.addWidget(SectionTitle("Runtime"))

        self.runtime = self._field("STATE", RuntimeBadge())
        self.mode = self._field("MODE", StatusPill("PAPER", "warn"))
        self.heartbeat = self._field("HEARTBEAT", QLabel("Awaiting runtime"))
        self.broker = self._field("BROKER", QLabel("Disconnected"))
        self.runtime_badge = self.runtime.value_label  # type: ignore[attr-defined]
        for field in (
            self.runtime,
            self.mode,
            self.heartbeat,
            self.broker,
        ):
            layout.addWidget(field)
        layout.addStretch(1)

        self.start_button = IconButton(
            f"{Icons.START}  Start Runtime",
            tooltip="Start the configured runtime",
            style="primary",
        )
        self.stop_button = IconButton(
            f"{Icons.STOP}  Stop Runtime",
            tooltip="Request an orderly runtime stop",
            style="danger",
        )
        self.start_button.clicked.connect(self.start_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        self.model = self._compatibility_card("Active Model")
        self.market = self._compatibility_card("Market Feed")
        self.cycles = self._compatibility_card("Runtime Cycles")
        self.risk = self._compatibility_card("Safety")
        self.render(RuntimeSnapshot.initial())

    @staticmethod
    def _field(label: str, value: QLabel) -> QFrame:
        frame = QFrame()
        field_layout = QVBoxLayout(frame)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(Spacing.XXS)
        title = QLabel(label)
        title.setObjectName("metricTitle")
        value.setObjectName(
            value.objectName() or "runtimeRibbonValue"
        )
        field_layout.addWidget(title)
        field_layout.addWidget(value)
        frame.value_label = value  # type: ignore[attr-defined]
        frame._value = value  # type: ignore[attr-defined]
        return frame

    def _compatibility_card(self, title: str) -> MetricCard:
        card = MetricCard(title)
        card.setParent(self)
        card.hide()
        return card

    def render(self, snapshot: RuntimeSnapshot) -> None:
        if not isinstance(snapshot, RuntimeSnapshot):
            raise TypeError("snapshot must be a RuntimeSnapshot")
        state = snapshot.state.value
        self.runtime.value_label.set_state(state)  # type: ignore[attr-defined]
        self.mode.value_label.set_status(  # type: ignore[attr-defined]
            snapshot.environment,
            "good" if snapshot.environment.upper() == "PAPER" else "warn",
        )
        self.heartbeat.value_label.setText(  # type: ignore[attr-defined]
            f"Cycle {snapshot.cycle_count}"
            if snapshot.cycle_count
            else "Awaiting runtime"
        )
        self.broker.value_label.setText(snapshot.broker_status)  # type: ignore[attr-defined]
        self.model.set_value(
            snapshot.active_model,
            snapshot.inference_status,
        )
        self.market.set_value(snapshot.market_feed_status)
        self.cycles.set_value(str(snapshot.cycle_count))
        self.risk.set_value(
            "Protected"
            if snapshot.emergency_stop_enabled
            else "Unprotected"
        )
        active = state in {"STARTING", "RUNNING", "STOPPING"}
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(state in {"STARTING", "RUNNING"})
