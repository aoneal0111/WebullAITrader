import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QPushButton,
)

from app.gui.models import TimelineRow, TimelineSnapshot
from app.gui.widgets.timeline_panel import TimelinePanel


APPLICATION = QApplication.instance() or QApplication([])


def test_panel_renders_newest_first_rows_without_controls_or_editing() -> None:
    panel = TimelinePanel()
    snapshot = TimelineSnapshot(
        rows=(
            TimelineRow(
                "16:00:02",
                "DECISION",
                "SUCCESS",
                "Newest event",
            ),
            TimelineRow(
                "16:00:01",
                "SYSTEM",
                "INFO",
                "Older event",
            ),
        ),
        max_entries=500,
    )

    panel.render(snapshot)

    assert panel.table.rowCount() == 2
    assert panel.table.item(0, 3).text() == "Newest event"
    assert panel.table.item(1, 3).text() == "Older event"
    assert panel.table.editTriggers() == (
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    assert panel.findChildren(QPushButton) == []
    panel.deleteLater()
