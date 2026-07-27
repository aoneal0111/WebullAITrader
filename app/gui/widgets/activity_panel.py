from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.gui.models import ActivitySnapshot


class ActivityPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._activity = QLabel("No operations events recorded.")
        self._activity.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._activity.setWordWrap(True)

        layout.addWidget(self._activity)

    def render(self, snapshot: ActivitySnapshot) -> None:
        if not snapshot.entries:
            self._activity.setText("No operations events recorded.")
            return

        self._activity.setText(
            "\n\n".join(
                f"{entry.occurred_at.astimezone():%H:%M:%S}   {entry.message}"
                for entry in snapshot.entries
            )
        )
