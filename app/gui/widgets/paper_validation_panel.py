from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from app.gui.models.paper_validation import PaperValidationDashboardSnapshot
from app.gui.widgets.common import StatusBadge


class PaperValidationPanel(QWidget):
    """Read-only status panel for the paper-account validation workflow."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.overall_badge = StatusBadge("NOT RUN")
        self.status_badges: dict[str, StatusBadge] = {}
        grid = QGridLayout()
        for column, label in enumerate(
            ("Account", "Orders", "Buying Power", "Positions", "Reconciliation")
        ):
            grid.addWidget(QLabel(label), 0, column)
            badge = StatusBadge("NOT RUN")
            self.status_badges[label] = badge
            grid.addWidget(badge, 1, column)
        self.message_label = QLabel("Not started")
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName("muted")
        layout.addWidget(self.overall_badge)
        layout.addLayout(grid)
        layout.addWidget(self.message_label)

    def render(self, snapshot: PaperValidationDashboardSnapshot) -> None:
        values = {
            "Account": snapshot.account,
            "Orders": snapshot.orders,
            "Buying Power": snapshot.buying_power,
            "Positions": snapshot.positions,
            "Reconciliation": snapshot.reconciliation,
        }
        for label, status in values.items():
            self.status_badges[label].set_status(status, _tone(status))
        self.overall_badge.set_status(f"Overall: {snapshot.overall}", _tone(snapshot.overall))
        self.message_label.setText(snapshot.message or "Paper validation in progress")


def _tone(status: str) -> str:
    return {"PASS": "good", "FAIL": "danger", "RUNNING": "warn"}.get(
        status.upper(), "neutral"
    )


__all__ = ["PaperValidationPanel"]
