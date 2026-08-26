import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
import pytest

from app.gui.models import ActivityEntry, ActivitySnapshot, PortfolioDashboardSnapshot, WatchlistRow, WatchlistSnapshot
from app.gui.pages.dashboard import DashboardPage


@pytest.fixture
def application():
    app = QApplication.instance() or QApplication([])
    return app


def _candidate() -> WatchlistRow:
    return WatchlistRow(
        symbol="PMI", selected=True, latest_price="4.72", change="0.73",
        change_percent="+18.3%", bid="4.70", ask="4.73", volume="3.8M",
        market_status="OPEN", last_update="10:42:31 ET", stale="LIVE",
        rank="1", score="91", relative_volume="8.4x", dollar_volume="$17.6M",
        spread="0.8%", catalyst="News", passed_rules="Momentum; RVOL; HOD",
        failed_rules="Entry trigger", freshness="1.2s", session="PRE-MARKET",
        classification="QUALIFYING", float_shares="5.4M", setup="HOD BREAK",
        setup_state="ARMED", distance_to_hod="-2.1%", strategy_status="WAIT",
        explanations="High-momentum candidate with strong relative volume.",
        entry_trigger="> $4.79", stop_price="$4.49",
        blocking_reasons="Entry trigger not reached",
    )


def test_populated_candidate_renders_without_overflow(application: QApplication) -> None:
    dashboard = DashboardPage()
    dashboard.resize(1920, 1080)
    dashboard.market_workspace.render(
        WatchlistSnapshot(rows=(_candidate(),), candidate_count=1, scanner_status="Active")
    )
    dashboard.market_workspace.activity_panel.render(ActivitySnapshot(entries=(
        ActivityEntry(datetime.now(timezone.utc), "BUY filled", "TRADES", related_symbol="PMI"),
        ActivityEntry(datetime.now(timezone.utc), "Entry conditions satisfied", "DECISIONS", related_symbol="PMI"),
    )))
    dashboard.market_workspace.portfolio_summary.render(PortfolioDashboardSnapshot(
        metrics=(("Equity", "$25,000"), ("Buying Power", "$18,000"), ("Open Positions", "1"), ("Total P/L", "+$240"), ("Unrealized P/L", "+$180"), ("Realized P/L", "+$60")),
        highlights=(("Exposure", "$4,720"),),
    ))
    dashboard.show()
    application.processEvents()

    panel = dashboard.market_workspace.trade_intelligence
    assert panel._symbol.text() == "PMI"
    assert panel._price.text() == "$4.72"
    assert panel._header_metrics["Atlas score"].text() == "91"
    assert panel._decision.text() == "WAIT"
    assert panel._blocking.text() == "Entry trigger not reached"
    assert dashboard.market_workspace.activity_panel._table.rowCount() == 2
    assert dashboard.market_workspace.portfolio_summary._cards["Total P/L"]._value.text() == "+$240"
    assert dashboard.findChildren(type(dashboard)) == []
