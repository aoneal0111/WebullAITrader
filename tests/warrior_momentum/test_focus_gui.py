from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.models import WatchlistRow, WatchlistSnapshot
from app.gui.widgets.market_workspace import MarketWorkspace


def test_warrior_focus_uses_extended_columns_and_operator_selection() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    selected: list[str] = []
    workspace.operator_symbol_selected.connect(selected.append)
    workspace.render(WatchlistSnapshot(rows=(WatchlistRow(
        symbol="XYZ", selected=False, latest_price="10.20", change="+2.20",
        change_percent="+27.50%", bid="10.18", ask="10.22", volume="1,000,000",
        market_status="OPEN", last_update="09:45:00", stale="LIVE", rank="1",
        score="82.4", relative_volume="10.0x", dollar_volume="$10,200,000",
        spread="+0.39%", catalyst="TRUE", freshness="LIVE", session="REGULAR",
        float_shares="6.0M", setup="BULL FLAG", setup_state="FORMING",
        distance_to_hod="+0.70%", strategy_status="SETUP FORMING",
        explanations="Ranked #1 | Bull flag forming",
    ),)))
    assert workspace.watchlist._table.columnCount() == 20
    assert workspace.watchlist._table.item(0, 17).text() == "SETUP FORMING"
    workspace.watchlist._select_row(0, 0)
    assert selected == ["XYZ"]


def test_focus_mode_switch_preserves_current_atlas_and_warrior_selection() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    atlas = WatchlistSnapshot(rows=(WatchlistRow(
        symbol="ATLS", selected=False, latest_price="5", change="1",
        change_percent="20%", bid="4.9", ask="5.1", volume="100",
        market_status="OPEN", last_update="now", stale="LIVE", rank="1",
    ),))
    warrior = type("View", (), {
        "focus": WatchlistSnapshot(rows=(WatchlistRow(
            symbol="WARR", selected=False, latest_price="8", change="2",
            change_percent="30%", bid="7.9", ask="8.1", volume="200",
            market_status="OPEN", last_update="now", stale="LIVE", rank="1",
            strategy_status="TRIGGERED", blocking_reasons="catalyst, session",
        ),)),
        "summary": "Today: 0 trades · N/A", "funnel": "D 1 → Trig 1",
        "research": "Triggered but blocked: 1",
    })()
    selected = []
    workspace.operator_symbol_selected.connect(selected.append)
    workspace.render(atlas)
    workspace.render_warrior(warrior)
    workspace.watchlist._mode_selector.setCurrentText("WARRIOR PAPER")
    assert workspace.watchlist._table.item(0, 1).text() == "WARR"
    assert workspace.watchlist._paper_summary.text().endswith("N/A")
    workspace.watchlist._select_row(0, 1)
    workspace.watchlist._mode_selector.setCurrentText("CURRENT ATLAS")
    assert workspace.watchlist._table.item(0, 1).text() == "ATLS"
    assert selected == ["WARR"]
