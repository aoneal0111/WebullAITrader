import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter, QWidget

from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_production_main_window_keeps_trade_intelligence_in_middle_row(
    application, tmp_path
) -> None:
    settings_path = tmp_path / "production-layout.ini"

    # Reproduce the pre-versioned production state that collapsed the old
    # chart/scanner split's second child. Dashboard-only previews never load it.
    stale_splitter = QSplitter(Qt.Orientation.Horizontal)
    stale_splitter.addWidget(QWidget())
    stale_splitter.addWidget(QWidget())
    stale_splitter.resize(1515, 500)
    stale_splitter.setSizes((1515, 0))
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    settings.setValue("layout/responsive_mode", "wide")
    settings.setValue(
        "layout/chart_scanner_splitter", stale_splitter.saveState()
    )
    settings.sync()

    composition = create_desktop_composition()
    window = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.trading_service,
        composition.order_command_factory,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    try:
        window.resize(1536, 1024)
        window.show()
        application.processEvents()
        application.processEvents()

        workspace = window.dashboard.market_workspace
        middle = workspace.middle_splitter
        left = workspace.left_stack
        trade = workspace.market_section

        assert window.pages.widget(0) is window.dashboard
        assert middle.orientation() == Qt.Orientation.Horizontal
        assert middle.count() == 2
        assert middle.widget(0) is left
        assert middle.widget(1) is trade
        assert middle.indexOf(trade) == 1
        assert trade.parentWidget() is middle

        assert trade.isVisible()
        assert trade.width() > 600
        assert trade.height() > 300
        assert workspace.trade_intelligence.isVisible()
        assert workspace.opportunities_section.isVisible()
        assert 350 < workspace.opportunities_section.width() < 600
        assert workspace.market_overview_section.isVisible()
        assert abs(
            workspace.market_overview_section.width()
            - workspace.opportunities_section.width()
        ) <= 2

        total = sum(middle.sizes())
        left_ratio = middle.sizes()[0] / total
        right_ratio = middle.sizes()[1] / total
        assert 0.28 <= left_ratio <= 0.36
        assert 0.64 <= right_ratio <= 0.72

        section_scrolls = {
            workspace.market_overview_section.scroll_area,
            workspace.market_section.scroll_area,
            workspace.portfolio_section.scroll_area,
        }
        assert section_scrolls <= set(window.dashboard.findChildren(QScrollArea))
        assert all(
            area.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAsNeeded
            for area in section_scrolls
        )
        assert (
            workspace.watchlist._table.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        assert (
            workspace.activity_panel._table.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
    finally:
        window.close()
        composition.close()
