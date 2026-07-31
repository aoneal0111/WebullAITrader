import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from app.gui.models import (
    HealthDashboardSnapshot,
    OrdersSnapshot,
    PaperValidationDashboardSnapshot,
    PortfolioDashboardSnapshot,
    PositionsSnapshot,
    RuntimeSnapshot,
    WatchlistRow,
    WatchlistSnapshot,
)
from app.gui.pages.dashboard import DashboardPage
from app.gui.widgets.infrastructure_strip import InfrastructureStrip
from app.gui.widgets.market_workspace import MarketWorkspace
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.paper_validation_panel import PaperValidationPanel
from app.gui.widgets.portfolio_summary_strip import PortfolioSummaryStrip
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.runtime_control_header import RuntimeControlHeader


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("mode", ("TEST", "PAPER", "LIVE", "PRODUCTION"))
def test_runtime_header_displays_truthful_mode(application, mode) -> None:
    del application
    header = RuntimeControlHeader()
    header.render(replace(RuntimeSnapshot.initial(), environment=mode))

    assert header._metrics["Mode"].text() == mode
    assert header._metrics["Mode"].property("status") in {
        "good", "warn", "danger"
    }


@pytest.mark.parametrize(
    ("status", "tone"),
    (("CONNECTED", "good"), ("DEGRADED", "warn"), ("DISABLED", "neutral")),
)
def test_infrastructure_state_palette(application, status, tone) -> None:
    del application
    strip = InfrastructureStrip()
    strip.render(
        HealthDashboardSnapshot(
            overall_status=status,
            status_level=tone,
            metrics=(("Market Data", status),),
            incident="No incidents.",
        )
    )

    card = strip._cards["Market Data"]
    assert status in card._indicator.text()
    assert card._indicator.property("status") == tone


@pytest.mark.parametrize(
    ("value", "tone"),
    (("+$12.50", "good"), ("-$7.25", "danger")),
)
def test_portfolio_pnl_uses_directional_color(application, value, tone) -> None:
    del application
    strip = PortfolioSummaryStrip()
    strip.render(
        PortfolioDashboardSnapshot(
            metrics=(("Total P/L", value),),
            highlights=(),
        )
    )

    assert strip._cards["Total P/L"]._value.property("tone") == tone


def test_compact_watchlist_and_market_summary_use_same_snapshot(application) -> None:
    del application
    workspace = MarketWorkspace()
    snapshot = WatchlistSnapshot(
        rows=(
            WatchlistRow(
                symbol="SPY", selected=True, latest_price="500.00",
                change="+2.00", change_percent="+0.40%", bid="499.99",
                ask="500.01", volume="100", market_status="OPEN",
                last_update="10:00:00", stale="LIVE",
            ),
        )
    )
    workspace.render(snapshot)

    assert workspace.watchlist._table.columnCount() == 4
    assert workspace.watchlist._table.item(0, 2).text() == "+2.00"
    value, change = workspace.market_summary._rows["SPY"]
    assert (value.text(), change.text()) == ("500.00", "+0.40%")
    assert workspace.market_summary._rows["QQQ"][0].text() == "—"


def test_operator_tables_expose_reference_columns_and_real_rows(application) -> None:
    del application
    positions = PositionsPanel()
    positions.render(PositionsSnapshot(rows=((
        "XYZ", "LONG", "2", "$10.00", "$11.00", "+$2.00", "+10.00%",
    ),)))
    orders = OrdersPanel()
    orders.render(OrdersSnapshot(rows=((
        "XYZ", "BUY", "LIMIT", "$10.00", "2", "WORKING",
    ),)))

    assert positions._table.columnCount() == 7
    assert positions._table.item(0, 0).text() == "XYZ"
    assert orders._table.columnCount() == 6
    assert orders._table.item(0, 5).text() == "WORKING"


def test_health_diagnostics_and_paper_validation_remain_visible(application) -> None:
    del application
    dashboard = DashboardPage()
    diagnostics = HealthDashboardSnapshot(
        overall_status="DEGRADED",
        status_level="warn",
        metrics=(
            ("Trading Environment", "PAPER"),
            ("Market Data Environment", "PRODUCTION"),
            ("Trading REST", "CONNECTED"),
            ("Streaming", "DEGRADED"),
            ("Subscription", "ACCEPTED"),
            ("Entitlement", "GRANTED"),
            ("Probe SPY", "SUPPORTED"),
            ("Scanner", "READY"),
        ),
        incident="Stream reconnecting.",
    )
    dashboard.operator_health_panel.render(diagnostics)
    labels = {label.text() for label in dashboard.operator_health_panel.findChildren(QLabel)}
    assert {name for name, _ in diagnostics.metrics}.issubset(labels)

    panel: PaperValidationPanel = dashboard.paper_validation_panel
    panel.render(PaperValidationDashboardSnapshot(
        account="PASS", orders="PASS", buying_power="PASS",
        positions="PASS", reconciliation="PASS", overall="PASS",
        message="Validation completed.",
    ))
    assert panel.overall_badge.text() == "OVERALL: PASS"
    assert panel.status_badges["Orders"].text() == "PASS"


def test_dashboard_and_market_workspace_switch_responsive_orientation(application) -> None:
    page = DashboardPage()
    page.resize(1600, 900)
    page.show()
    application.processEvents()
    assert page.summary_splitter.orientation() == Qt.Orientation.Horizontal

    page.resize(1000, 720)
    application.processEvents()
    assert page.summary_splitter.orientation() == Qt.Orientation.Vertical

    market = MarketWorkspace()
    market.resize(1000, 500)
    market.show()
    application.processEvents()
    assert market.splitter.orientation() == Qt.Orientation.Horizontal
    market.resize(700, 700)
    application.processEvents()
    assert market.splitter.orientation() == Qt.Orientation.Vertical


def test_production_gui_contains_no_reference_sample_values() -> None:
    forbidden = ("125,430.50", "1,247.35", "AAPL 229.96", "41.3%")
    root = os.path.join(os.path.dirname(__file__), "..", "..", "app", "gui")
    for directory, _, files in os.walk(root):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(directory, filename), encoding="utf-8") as source:
                contents = source.read()
            assert not any(value in contents for value in forbidden)
