import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.gui.models import WatchlistRow, WatchlistSnapshot
from app.gui.pages.dashboard import DashboardPage
from app.gui.widgets.market_workspace import ChartPlaceholder, MarketWorkspace
from app.gui.widgets.trade_intelligence_panel import TradeIntelligencePanel


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def candidate(**overrides) -> WatchlistRow:
    values = {
        "symbol": "PMI",
        "selected": True,
        "latest_price": "4.72",
        "change": "+0.73",
        "change_percent": "+18.30%",
        "bid": "4.70",
        "ask": "4.73",
        "volume": "3,800,000",
        "market_status": "OPEN",
        "last_update": "10:42:31",
        "stale": "LIVE",
        "rank": "1",
        "score": "91.00",
        "relative_volume": "8.40x",
        "dollar_volume": "$17,600,000",
        "spread": "0.80%",
        "catalyst": "NEWS",
        "passed_rules": "price, volume, relative volume",
        "failed_rules": "--",
        "freshness": "LIVE",
        "session": "PREMARKET",
        "classification": "QUALIFYING",
        "float_shares": "5.4M",
        "setup": "HOD BREAK",
        "setup_state": "ARMED",
        "distance_to_hod": "-2.10%",
        "strategy_status": "WAITING",
        "explanations": "Entry trigger has not been reached.",
        "float_provenance": "AUTHORITATIVE FLOAT",
        "entry_trigger": "4.7900",
        "stop_price": "4.4900",
        "blocking_reasons": "entry trigger not reached",
        "warrior_evaluated": True,
        "warrior_score": "76.55",
        "warrior_status": "SETUP FORMING",
        "warrior_session": "PREMARKET",
        "strategy_name": "Warrior Momentum",
    }
    values.update(overrides)
    return WatchlistRow(**values)


def test_primary_workspace_replaces_chart_with_trade_intelligence(application) -> None:
    del application
    workspace = MarketWorkspace()

    assert isinstance(workspace.trade_intelligence, TradeIntelligencePanel)
    assert workspace.findChildren(ChartPlaceholder) == []
    assert workspace.market_section.heading.text() == "ATLAS TRADE INTELLIGENCE"
    assert workspace.focus_section.heading.text() == "OPPORTUNITIES"
    assert not hasattr(workspace, "focus_chart_button")


def test_candidate_details_render_authoritative_strategy_state(application) -> None:
    del application
    panel = TradeIntelligencePanel()
    panel.render(candidate())

    assert panel._symbol.text() == "PMI"
    assert panel._price.text() == "$4.72"
    assert panel._change.text() == "+18.30%"
    assert panel._header_metrics["Rank"].text() == "#1"
    assert panel._header_metrics["Scanner score"].text() == "91.00"
    assert panel._header_metrics["Scanner status"].text() == "QUALIFYING"
    assert panel._header_metrics["Warrior momentum"].text() == "SETUP FORMING"
    assert panel._decision.text() == "WAIT"
    assert panel._market_values["Relative volume"].text() == "8.40x"
    assert panel._market_values["Float"].text() == "5.4M"
    assert panel._plan_values["Setup"].text() == "HOD BREAK"
    assert panel._plan_values["Setup state"].text() == "ARMED"
    assert panel._plan_values["Entry trigger"].text() == "4.7900"
    assert panel._plan_values["Stop"].text() == "4.4900"
    assert panel._plan_values["Strategy"].text() == "Warrior Momentum"
    assert tuple(panel._plan_values) == (
        "Strategy", "Setup", "Setup state", "Strategy status",
        "Entry trigger", "Stop",
    )
    assert "entry trigger not reached" in panel._blocking.text()
    assert "relative volume" in panel._passed_rules.text()


def test_unavailable_candidate_values_remain_unavailable(application) -> None:
    del application
    panel = TradeIntelligencePanel()
    panel.render(candidate(
        score="--", catalyst="--", float_shares="--",
        entry_trigger="--", stop_price="--", blocking_reasons="--",
    ))

    assert panel._header_metrics["Scanner score"].text() == "--"
    assert panel._watching_values["Catalyst"].text() == "--"
    assert panel._market_values["Float"].text() == "--"
    assert panel._plan_values["Entry trigger"].text() == "--"
    assert panel._plan_values["Stop"].text() == "--"
    assert panel._blocking.text() == "--"


def test_identical_snapshots_skip_table_and_detail_rebuilds(application) -> None:
    del application
    workspace = MarketWorkspace()
    snapshot = WatchlistSnapshot(rows=(candidate(),), candidate_count=1)
    workspace.render(snapshot)
    table_renders = workspace.watchlist._render_count
    detail_renders = workspace.trade_intelligence._render_count

    workspace.render(snapshot)

    assert workspace.watchlist._render_count == table_renders
    assert workspace.trade_intelligence._render_count == detail_renders


def test_selection_populates_details_once_without_repeated_propagation(application) -> None:
    del application
    workspace = MarketWorkspace()
    selected = []
    workspace.operator_symbol_selected.connect(selected.append)
    snapshot = WatchlistSnapshot(rows=(
        candidate(symbol="PMI", selected=True),
        candidate(symbol="XYZ", selected=False, rank="2", score="84.00"),
    ), candidate_count=2)
    workspace.render(snapshot)

    workspace.watchlist._select_row(1, 0)
    workspace.watchlist._select_row(1, 0)

    assert workspace.trade_intelligence._symbol.text() == "XYZ"
    assert selected == ["XYZ"]
    assert workspace.watchlist._table.currentRow() == 1


def test_opportunity_selector_is_compact_and_has_no_placeholder_controls(application) -> None:
    del application
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(candidate(),), candidate_count=1))

    assert tuple(
        workspace.watchlist._table.horizontalHeaderItem(index).text()
        for index in range(workspace.watchlist._table.columnCount())
    ) == (
        "Rank", "Symbol", "Price", "Chg %", "RVOL", "Score", "Setup",
        "Status", "Freshness",
    )
    assert not hasattr(workspace.watchlist, "columns_button")
    assert not hasattr(workspace.watchlist, "filters_button")


def test_dashboard_keeps_real_controls_and_shows_authoritative_performance(application) -> None:
    del application
    dashboard = DashboardPage()

    assert not dashboard.portfolio_summary.isHidden()
    assert dashboard.runtime_header.resume_button.text() == "START"
    assert dashboard.runtime_header.stop_button.text() == "STOP"
    assert dashboard.runtime_header._metrics["Mode"].isHidden()
    assert not dashboard.market_workspace.runtime_controls.mode_label.isHidden()
    assert not dashboard.runtime_header._metrics["Risk"].isHidden()
    assert dashboard.runtime_header.pause_button.isHidden()
    assert dashboard.runtime_header.flatten_button.isHidden()
