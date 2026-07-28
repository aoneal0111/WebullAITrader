from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

from app.gui.theme import Sizing, Spacing


class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        value: str = "—",
        note: str = "",
    ) -> None:
        super().__init__()
        self.setObjectName("metricCard")
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
        layout.setSpacing(Spacing.XS)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("metricTitle")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        self.note_label = QLabel(note)
        self.note_label.setObjectName("metricNote")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.note_label)

        # Compatibility with the pre-foundation MetricCard API.
        self._value = self.value_label
        self._note = self.note_label

    def set_value(self, value: str, note: str | None = None) -> None:
        self.value_label.setText(str(value))
        if note is not None:
            self.note_label.setText(note)

