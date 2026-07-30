from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from app.gui.models import ReplayWorkspaceSnapshot


class ReplayStatusPanel(QWidget):
    """Render the dashboard's immutable replay status summary."""

    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._values = {
            "Status": QLabel(),
            "Position": QLabel(),
            "Speed": QLabel(),
            "Elapsed": QLabel(),
        }
        for column, (label, value) in enumerate(self._values.items()):
            name = QLabel(label)
            name.setObjectName("muted")
            layout.addWidget(name, 0, column)
            layout.addWidget(value, 1, column)

    def render(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        self._values["Status"].setText(snapshot.status)
        self._values["Position"].setText(snapshot.current_position)
        self._values["Speed"].setText(snapshot.replay_speed)
        self._values["Elapsed"].setText(snapshot.elapsed_time)


__all__ = ["ReplayStatusPanel"]
