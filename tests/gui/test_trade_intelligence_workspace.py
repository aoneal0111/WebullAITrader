import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from app.gui.models import (
    PositionsSnapshot,
    RuntimeState,
    WatchlistRow,
    WatchlistSnapshot,
)
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
    assert workspace.focus_section.heading.text() == "EQUITY SCANNER"
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


@pytest.mark.parametrize(
    ("width", "height"),
    ((1280, 720), (1366, 768), (1440, 900), (1920, 1080)),
)
def test_trade_intelligence_fits_width_and_scrolls_vertically(
    application, width, height
) -> None:
    dashboard = DashboardPage()
    dashboard.resize(width, height)
    dashboard.show()
    dashboard.market_workspace.render(
        WatchlistSnapshot(rows=(candidate(),), candidate_count=1)
    )
    application.processEvents()

    workspace = dashboard.market_workspace
    workspace.set_runtime_phase(RuntimeState.RUNNING, account_loaded=True)
    workspace.ensure_middle_composition()
    application.processEvents()
    scroll = workspace.market_section.scroll_area
    panel = workspace.trade_intelligence
    assert scroll is not None
    assert scroll.widgetResizable()
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert scroll.verticalScrollBar().maximum() > 0
    assert panel.width() <= scroll.viewport().width()

    labels = (
        *panel._header_metrics.values(),
        *panel._watching_values.values(),
        panel._passed_rules,
        panel._failed_rules,
        *panel._market_values.values(),
        *panel._plan_values.values(),
    )
    assert all(
        label.mapTo(panel, QPoint(0, 0)).x() + label.width() <= panel.width()
        for label in labels
    )

    sizes = workspace.middle_splitter.sizes()
    left_ratio = sizes[0] / sum(sizes)
    assert 0.58 <= left_ratio <= 0.62
    assert workspace.left_stack.width() >= 620
    assert workspace.positions_section.isVisible()
    assert workspace.positions_panel.activity_tabs.count() == 3
    assert workspace.lower_splitter.count() == 2
    assert workspace.opportunities_section.isVisible()
    assert workspace.crypto_scanner_section.isVisible()
    assert workspace.opportunities_section.geometry().right() < (
        workspace.crypto_scanner_section.geometry().left()
    )
    assert workspace.portfolio_section.isVisible()
    dashboard.close()


def test_trade_intelligence_lifecycle_empty_state_restores_metrics(
    application,
) -> None:
    del application
    panel = TradeIntelligencePanel()
    panel.set_runtime_phase(RuntimeState.STOPPED)
    assert not panel._lifecycle_empty.isHidden()
    assert panel._header.isHidden()
    assert "--" not in panel._lifecycle_empty.text()

    panel.set_runtime_phase(RuntimeState.RUNNING)
    panel.render(candidate())
    assert panel._lifecycle_empty.isHidden()
    assert not panel._header.isHidden()
    assert panel._header_metrics["Rank"].text() == "#1"

    panel.set_runtime_phase(RuntimeState.STOPPING)
    panel.set_runtime_phase(RuntimeState.STOPPED)
    assert "LAST SNAPSHOT" in panel._lifecycle_empty.text()
    assert "STALE" in panel._lifecycle_empty.text()
    panel.close()


def test_trade_intelligence_distinguishes_starting_and_running_without_candidate(
    application,
) -> None:
    del application
    panel = TradeIntelligencePanel()

    panel.set_runtime_phase(RuntimeState.STARTING)
    panel.render(None)
    assert "Initializing market analysis" in panel._lifecycle_empty.text()
    assert panel._header.isHidden()
    assert panel._body_widget.isHidden()

    panel.set_runtime_phase(RuntimeState.RUNNING)
    assert "NO EQUITY OPPORTUNITY SELECTED" in panel._lifecycle_empty.text()
    assert "Atlas is monitoring the market" in panel._lifecycle_empty.text()
    assert panel._header.isHidden()
    assert panel._body_widget.isHidden()
    assert "--" not in panel._lifecycle_empty.text()

    panel.render(candidate())
    assert panel._lifecycle_empty.isHidden()
    assert not panel._header.isHidden()
    assert not panel._body_widget.isHidden()
    assert panel._header_metrics["Rank"].text() == "#1"

    panel.render(None)
    assert "NO EQUITY OPPORTUNITY SELECTED" in panel._lifecycle_empty.text()
    assert panel._header.isHidden()
    assert panel._body_widget.isHidden()
    panel.close()


@pytest.mark.parametrize(
    ("width", "height"),
    ((1280, 720), (1366, 768), (1440, 900), (1920, 1080)),
)
def test_running_empty_workstation_is_intentional_at_supported_sizes(
    application, width, height,
) -> None:
    dashboard = DashboardPage()
    dashboard.resize(width, height)
    dashboard.show()
    workspace = dashboard.market_workspace
    workspace.set_runtime_phase(
        RuntimeState.RUNNING,
        positions_synchronized=True,
        positions_status="AVAILABLE",
    )
    workspace.render(WatchlistSnapshot())
    workspace.positions_panel.render(PositionsSnapshot.initial())
    workspace.ensure_middle_composition()
    application.processEvents()

    intelligence = workspace.trade_intelligence
    scroll = workspace.market_section.scroll_area
    assert "NO EQUITY OPPORTUNITY SELECTED" in intelligence._lifecycle_empty.text()
    assert intelligence._header.isHidden()
    assert intelligence._body_widget.isHidden()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert workspace.positions_panel._symbol.text() == "NO ACTIVE POSITION"
    assert workspace.positions_panel._position_state.text() == "NO ACTIVE POSITION"
    assert "AWAITING" not in workspace.positions_panel._table._empty_state.text()
    assert workspace.positions_panel._table._empty_state.text().startswith(
        "NO ACTIVE POSITION"
    )
    sizes = workspace.middle_splitter.sizes()
    assert 0.58 <= sizes[0] / sum(sizes) <= 0.62
    assert workspace.lower_splitter.count() == 2
    assert workspace.opportunities_section.geometry().right() < (
        workspace.crypto_scanner_section.geometry().left()
    )
    assert workspace.portfolio_section.isVisible()
    dashboard.close()


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
