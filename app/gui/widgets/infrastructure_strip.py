from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from app.gui.models import HealthDashboardSnapshot
from app.gui.widgets.common import StatusCard


class InfrastructureStrip(QWidget):
    """Render infrastructure health facts into focused status cards."""

    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._cards = {
            "Market Data": StatusCard("Data Feed"),
            "Broker": StatusCard("Broker"),
            "Risk": StatusCard("Risk Engine"),
            "AI": StatusCard("AI Engine"),
        }
        for column, card in enumerate(self._cards.values()):
            layout.addWidget(card, 0, column)
            layout.setColumnStretch(column, 1)

    def render(self, snapshot: HealthDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
        detail = {
            "Market Data": f"Heartbeat {metrics.get('Heartbeat', '--')}",
            "Broker": f"Latency {metrics.get('Latency', '--')}",
            "Risk": snapshot.incident,
            "AI": "Inference service",
        }
        for key, card in self._cards.items():
            status = metrics.get(key, "--")
            if status == "--":
                status = "UNKNOWN"
            card.set_status(
                status,
                level=_status_level(status),
                detail=detail[key],
            )


def _status_level(status: str) -> str:
    normalized = status.upper()
    if normalized in {"CONNECTED", "READY", "RUNNING", "HEALTHY"}:
        return "good"
    if normalized in {
        "DISCONNECTED",
        "FAILED",
        "ERROR",
        "UNAVAILABLE",
    }:
        return "danger"
    if normalized in {"DEGRADED", "RECONNECTING", "STARTING"}:
        return "warn"
    return "neutral"


__all__ = ["InfrastructureStrip"]
