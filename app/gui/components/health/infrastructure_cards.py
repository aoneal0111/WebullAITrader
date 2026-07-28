from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from app.event_store import EventStoreSnapshot
from app.gui.components.cards import StatusCard
from app.gui.models import HealthCenterSnapshot, RuntimeSnapshot
from app.recording import RecordingSnapshot
from app.gui.theme import Spacing


class InfrastructureCards(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.SM)
        self.cards = {
            "broker": StatusCard("Broker"),
            "market_data": StatusCard("Market Data"),
            "operations_bus": StatusCard("Operations Bus"),
            "recorder": StatusCard("Recorder"),
            "scanner": StatusCard("Scanner"),
            "event_store": StatusCard("Event Store"),
        }
        for index, card in enumerate(self.cards.values()):
            layout.addWidget(card, 0, index)
            layout.setColumnStretch(index, 1)

    def render(
        self,
        runtime: RuntimeSnapshot,
        health: HealthCenterSnapshot,
        recording: RecordingSnapshot,
        event_store: EventStoreSnapshot,
    ) -> None:
        self.cards["broker"].set_status(
            runtime.broker_status,
            health.broker_status.level,
        )
        self.cards["market_data"].set_status(
            runtime.market_feed_status,
            health.market_data_status.level,
        )
        self.cards["operations_bus"].set_status(
            health.operations_bus_status.value,
            health.operations_bus_status.level,
        )
        recorder_status = recording.status.value
        self.cards["recorder"].set_status(
            recorder_status,
            _level(recorder_status),
            f"{recording.event_count} events",
        )
        self.cards["scanner"].set_status(
            health.scanner_status.value,
            health.scanner_status.level,
        )
        event_status = event_store.status.value
        self.cards["event_store"].set_status(
            event_status,
            _level(event_status),
            f"{event_store.statistics.total_events} indexed events",
        )


def _level(status: str) -> str:
    normalized = status.upper()
    if normalized in {"ACTIVE", "READY", "RUNNING", "HEALTHY"}:
        return "good"
    if normalized in {"ERROR", "FAILED", "CLOSED"}:
        return "danger"
    if normalized in {"EMPTY", "IDLE", "STOPPED", "COMPLETED"}:
        return "warn"
    return "neutral"
