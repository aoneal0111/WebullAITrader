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


@pytest.mark.parametrize("width,height", ((1536, 1024), (1920, 1080)))
def test_populated_candidate_renders_without_overflow(
    application: QApplication, width: int, height: int
) -> None:
    dashboard = DashboardPage()
    dashboard.resize(width, height)
    dashboard.market_workspace.render(
        WatchlistSnapshot(rows=(_candidate(),), candidate_count=1, scanner_status="Active")
    )
    dashboard.market_workspace.activity_panel.render(ActivitySnapshot(entries=(
        ActivityEntry(datetime.now(timezone.utc), "BUY filled", "TRADES", related_symbol="PMI"),
        ActivityEntry(datetime.now(timezone.utc), "Entry conditions satisfied", "DECISIONS", related_symbol="PMI"),
    )))
    dashboard.market_workspace.portfolio_summary.render(PortfolioDashboardSnapshot(
        metrics=(("Equity", "$25,000"), ("Cash", "$20,280"),
                 ("Buying Power", "$18,000"), ("Open Positions", "1"),
                 ("Total P/L", "+$240"), ("Unrealized P/L", "+$180"),
                 ("Realized P/L", "+$60"), ("Current Drawdown", "0.4%"),
                 ("Win Rate", "67%")),
        highlights=(("Exposure", "$4,720"), ("Gross Exposure", "$4,720"),
                    ("Net Exposure", "$4,720"),
                    ("Winning / Losing Positions", "2 / 1")),
    ))
    dashboard.show()
    application.processEvents()

    panel = dashboard.market_workspace.trade_intelligence
    assert panel._symbol.text() == "PMI"
    assert panel._price.text() == "$4.72"
    assert panel._header_metrics["Scanner score"].text() == "91"
    assert panel._decision.text() == "EVALUATING"
    assert panel._blocking.text() == "--"
    assert dashboard.market_workspace.activity_panel._table.rowCount() == 2
    assert dashboard.market_workspace.portfolio_summary._cards["Total P/L"]._value.text() == "+$240"
    assert 100 <= dashboard.runtime_header.height() <= 125

    critical = (
        (panel._symbol, 20),
        (panel._reason, 32),
        (panel._passed_rules, 16),
        (panel._failed_rules, 16),
        (panel._market_values["Last"], 16),
        (panel._market_values["Freshness"], 16),
        (panel._plan_values["Entry trigger"], 16),
        (panel._plan_values["Stop"], 16),
        (panel._decision, 40),
        (panel._blocking, 16),
        (panel._autonomous_paper, 16),
    )
    for widget, readable_height in critical:
        assert widget.isVisible()
        assert widget.height() >= readable_height

    contained = (
        (panel._watching, panel._reason),
        *( (panel._watching, value) for value in panel._watching_values.values() ),
        (panel._watching, panel._passed_rules),
        (panel._watching, panel._failed_rules),
        *( (panel._market, value) for value in panel._market_values.values() ),
        *( (panel._plan, value) for value in panel._plan_values.values() ),
        (panel._decision_panel, panel._decision),
        (panel._decision_panel, panel._decision_explanation),
        (panel._decision_panel, panel._blocking),
        (panel._decision_panel, panel._autonomous_paper),
    )
    for card, widget in contained:
        widget_bottom = widget.mapToGlobal(widget.rect().bottomLeft()).y()
        card_bottom = card.mapToGlobal(card.rect().bottomLeft()).y()
        assert widget_bottom <= card_bottom

    workspace = dashboard.market_workspace
    assert workspace.runtime_controls_section.isHidden()
    assert 0.58 <= (
        workspace.opportunities_section.height()
        / (workspace.opportunities_section.height() + workspace.market_overview_section.height())
    ) <= 0.65
    assert workspace.market_section.height() >= 470
    assert workspace.activity_section.height() >= 280
    assert workspace.activity_panel._table.height() >= 150
    assert workspace.portfolio_section.height() >= 280
    assert workspace.portfolio_summary._columns == 3
    for card in workspace.portfolio_summary._card_order:
        assert card.height() >= 48
        assert card._value.isVisible()
        assert card._value.height() >= 16
    assert dashboard.findChildren(type(dashboard)) == []


@pytest.mark.parametrize("width,height", ((1536, 1024), (1920, 1080)))
def test_empty_workstation_keeps_scanner_and_activity_states_compact(
    application: QApplication, width: int, height: int
) -> None:
    dashboard = DashboardPage()
    dashboard.resize(width, height)
    dashboard.show()
    application.processEvents()

    workspace = dashboard.market_workspace
    assert workspace.watchlist._table._empty_state.isVisible()
    assert workspace.opportunities_section.height() < workspace.market_section.height()
    assert workspace.activity_panel._table._empty_state.isVisible()
    assert workspace.activity_section.height() < workspace.height() / 2
    assert workspace.portfolio_summary._columns == 3
    assert all(card.height() >= 48 for card in workspace.portfolio_summary._card_order)
