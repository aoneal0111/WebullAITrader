import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QPushButton,
)

from app.gui.models import (
    LifecycleEntryRow,
    LifecycleExplorerSnapshot,
    LifecycleRow,
)
from app.gui.widgets.trade_lifecycle_panel import TradeLifecyclePanel


APPLICATION = QApplication.instance() or QApplication([])


def test_panel_renders_expandable_read_only_lifecycles() -> None:
    panel = TradeLifecyclePanel()
    snapshot = LifecycleExplorerSnapshot(
        rows=(
            LifecycleRow(
                symbol="AAPL",
                status="OPEN",
                opened="17:00:00",
                closed="--",
                realized_pnl="+$42.13",
                entries=(
                    LifecycleEntryRow(
                        time="17:00:00",
                        phase="DECISION",
                        summary="Enter long",
                    ),
                    LifecycleEntryRow(
                        time="17:00:01",
                        phase="FILLED",
                        summary="Order filled",
                    ),
                ),
            ),
            LifecycleRow(
                symbol="NVDA",
                status="CLOSED",
                opened="17:10:00",
                closed="17:20:00",
                realized_pnl="+$118.40",
                entries=(),
            ),
        ),
        selected_symbol="AAPL",
    )

    panel.render(snapshot)

    assert panel.tree.topLevelItemCount() == 2
    aapl = panel.tree.topLevelItem(0)
    assert aapl.text(0) == "AAPL"
    assert aapl.text(1) == "OPEN"
    assert aapl.childCount() == 2
    assert aapl.child(1).text(1) == "FILLED"
    assert aapl.isExpanded()
    assert not panel.tree.topLevelItem(1).isExpanded()
    assert panel.tree.editTriggers() == (
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    assert panel.findChildren(QPushButton) == []
    panel.deleteLater()
