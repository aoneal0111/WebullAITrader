"""Compatibility widgets backed by the Atlas X component library."""

from app.gui.components.cards import MetricCard
from app.gui.components.common import StatusPill


class StatusBadge(StatusPill):
    def __init__(self, text: str = "UNKNOWN") -> None:
        super().__init__(text)
        self.setObjectName("statusBadge")


__all__ = ["MetricCard", "StatusBadge"]
