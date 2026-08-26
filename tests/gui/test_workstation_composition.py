import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from app.gui.pages.dashboard import DashboardPage


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_dashboard_is_a_non_scrolling_workstation(application) -> None:
    del application
    dashboard = DashboardPage()

    assert not dashboard.findChildren(QScrollArea)
    assert dashboard.workstation_header is dashboard.runtime_header
    assert dashboard.market_workspace.left_column is not None
    assert dashboard.market_workspace.right_workspace is not None
    assert dashboard.workstation_footer is not None


def test_workstation_exposes_reference_panels(application) -> None:
    del application
    dashboard = DashboardPage()
    workspace = dashboard.market_workspace

    assert workspace.opportunities_section.heading.text() == "OPPORTUNITIES"
    assert workspace.market_overview_section.heading.text() == "MARKET OVERVIEW"
    assert workspace.runtime_controls_section.heading.text() == "RUNTIME CONTROLS"
    assert workspace.safety_section.heading.text() == "SAFETY"
    assert workspace.market_section.heading.text() == "ATLAS TRADE INTELLIGENCE"
    assert workspace.trade_intelligence._watching.heading.text() == "WHY ATLAS IS WATCHING"
    assert workspace.trade_intelligence._market.heading.text() == "CURRENT MARKET CONDITIONS"
    assert workspace.trade_intelligence._plan.heading.text() == "TRADE PLAN"
    assert workspace.trade_intelligence._decision_panel.heading.text() == "CURRENT DECISION"
    assert workspace.activity_section.heading.text() == "LIVE AUTONOMOUS ACTIVITY"
    assert workspace.portfolio_section.heading.text() == "PORTFOLIO / PERFORMANCE"


def test_workstation_header_has_compact_health_and_account_metrics(application) -> None:
    del application
    dashboard = DashboardPage()
    header = dashboard.runtime_header
    labels = set(header._metrics)

    assert {"Runtime", "Market Data", "Broker", "Scanner", "Risk"} <= labels
    assert {"Mode", "Equity", "Buying Power", "Local Time"} <= labels
    assert not header.settings_button.isHidden()
    assert not header.menu_button.isHidden()


def test_market_overview_is_honest_when_projection_is_unavailable(application) -> None:
    del application
    dashboard = DashboardPage()
    overview = dashboard.market_workspace.market_overview
    assert tuple(overview._rows) == ("SPY", "QQQ", "DIA", "VIX")
    assert all(value.text() == "--" for row in overview._values.values() for key, value in row.items() if key != "Instrument")
