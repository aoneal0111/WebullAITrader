from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from app.gui.models import AtlasReasoningSnapshot


class AtlasReasoningPanel(QWidget):
    """Permanent compact view of current projected operational reasoning."""

    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(4)
        self.current_action = self._row(layout, 0, "CURRENT ACTION")
        self.why = self._row(layout, 1, "WHY")
        self.risk_protection = self._row(layout, 2, "RISK / PROTECTION")
        self.next_trigger = self._row(layout, 3, "NEXT TRIGGER")
        layout.setColumnStretch(1, 1)

    @staticmethod
    def _row(layout: QGridLayout, row: int, title: str) -> QLabel:
        key = QLabel(title)
        key.setObjectName("metricTitle")
        value = QLabel("Unknown")
        value.setObjectName("monitorValue")
        value.setWordWrap(True)
        layout.addWidget(key, row, 0)
        layout.addWidget(value, row, 1)
        return value

    def render(self, snapshot: AtlasReasoningSnapshot) -> None:
        self.current_action.setText(snapshot.current_action)
        self.why.setText(snapshot.why)
        self.risk_protection.setText(snapshot.risk_protection)
        self.next_trigger.setText(snapshot.next_trigger)


__all__ = ["AtlasReasoningPanel"]
