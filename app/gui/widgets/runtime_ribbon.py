from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from app.gui.models import RuntimeSnapshot
from app.gui.widgets.common import MetricCard


class RuntimeRibbon(QWidget):
    def __init__(self) -> None:
        super().__init__()

        layout = QGridLayout(self)
        layout.setSpacing(12)

        self.runtime = MetricCard("Runtime", "Stopped", "")
        self.broker = MetricCard("Broker", "Disconnected", "")
        self.market = MetricCard("Market Feed", "Idle", "")
        self.model = MetricCard("Active Model", "Not loaded", "")
        self.cycles = MetricCard("Runtime Cycles", "0", "")
        self.risk = MetricCard("Safety", "Protected", "")

        cards = (
            self.runtime,
            self.broker,
            self.market,
            self.model,
            self.cycles,
            self.risk,
        )

        for index, card in enumerate(cards):
            layout.addWidget(card, index // 3, index % 3)

    def render(self, snapshot: RuntimeSnapshot) -> None:
        self.runtime.set_value(
            snapshot.state.value.title(),
            "Runtime lifecycle state",
        )

        self.broker.set_value(
            snapshot.broker_status,
            "Broker gateway",
        )

        self.market.set_value(
            snapshot.market_feed_status,
            "Market feed",
        )

        self.model.set_value(
            snapshot.active_model,
            snapshot.inference_status,
        )

        self.cycles.set_value(
            str(snapshot.cycle_count),
            "Completed runtime cycles",
        )

        self.risk.set_value(
            "Protected" if snapshot.emergency_stop_enabled else "Unprotected",
            "Emergency stop enabled"
            if snapshot.emergency_stop_enabled
            else "Emergency stop unavailable",
        )
