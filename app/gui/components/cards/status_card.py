from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

from app.gui.components.common import StatusPill
from app.gui.theme import Sizing, Spacing


class StatusCard(QFrame):
    def __init__(
        self,
        title: str,
        status: str = "UNKNOWN",
        level: str = "neutral",
        detail: str = "",
    ) -> None:
        super().__init__()
        self.setObjectName("statusCard")
        self.setMinimumWidth(Sizing.CARD_MIN_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.MD,
            Spacing.MD,
            Spacing.MD,
            Spacing.MD,
        )
        layout.setSpacing(Spacing.SM)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("metricTitle")
        self.status_pill = StatusPill(status, level)
        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("metricNote")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.status_pill)
        layout.addWidget(self.detail_label)

    def set_status(
        self,
        status: str,
        level: str = "neutral",
        detail: str | None = None,
    ) -> None:
        self.status_pill.set_status(status, level)
        if detail is not None:
            self.detail_label.setText(detail)

