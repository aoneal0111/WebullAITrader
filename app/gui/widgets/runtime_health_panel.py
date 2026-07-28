from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from app.gui.models import HealthBadgeSnapshot, HealthCenterSnapshot
from app.gui.widgets.common import StatusBadge


class RuntimeHealthPanel(QWidget):
    """Badge-only rendering of an immutable health-center snapshot."""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._grid = QGridLayout()
        self._grid.setSpacing(8)
        root.addLayout(self._grid)

        initial = HealthCenterSnapshot.initial()
        self.badges: dict[str, StatusBadge] = {}
        for index, badge_snapshot in enumerate(
            self._primary_badges(initial)
        ):
            label = QLabel(badge_snapshot.label.upper())
            label.setObjectName("metricTitle")
            badge = StatusBadge()
            self.badges[badge_snapshot.label] = badge
            column = index % 5
            row = (index // 5) * 2
            self._grid.addWidget(label, row, column)
            self._grid.addWidget(badge, row + 1, column)

        self._messages = QVBoxLayout()
        self._messages.setSpacing(6)
        root.addLayout(self._messages)
        self._message_badges: list[StatusBadge] = []
        self.render(initial)

    def render(self, snapshot: HealthCenterSnapshot) -> None:
        if not isinstance(snapshot, HealthCenterSnapshot):
            raise TypeError("snapshot must be a HealthCenterSnapshot")
        for badge_snapshot in self._primary_badges(snapshot):
            self.badges[badge_snapshot.label].set_status(
                badge_snapshot.value,
                badge_snapshot.level,
            )
        self._clear_messages()
        messages = snapshot.warnings + snapshot.errors
        if not messages:
            messages = (
                HealthBadgeSnapshot(
                    "Status",
                    "No warnings or errors",
                    "good",
                ),
            )
        for message in messages:
            badge = StatusBadge()
            badge.set_status(
                f"{message.label}: {message.value}",
                message.level,
            )
            self._messages.addWidget(badge)
            self._message_badges.append(badge)

    def message_texts(self) -> tuple[str, ...]:
        return tuple(badge.text() for badge in self._message_badges)

    def _clear_messages(self) -> None:
        for badge in self._message_badges:
            self._messages.removeWidget(badge)
            badge.deleteLater()
        self._message_badges.clear()

    @staticmethod
    def _primary_badges(
        snapshot: HealthCenterSnapshot,
    ) -> tuple[HealthBadgeSnapshot, ...]:
        return (
            snapshot.overall_health,
            snapshot.runtime_state,
            snapshot.broker_status,
            snapshot.scanner_status,
            snapshot.market_data_status,
            snapshot.operations_bus_status,
            snapshot.current_cycle,
            snapshot.last_completed_cycle,
            snapshot.last_update_time,
        )
