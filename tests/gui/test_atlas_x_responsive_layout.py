import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSizePolicy

from app.gui.pages.dashboard import DashboardPage


APPLICATION = QApplication.instance() or QApplication([])


def test_workstation_prefers_a_seventy_thirty_horizontal_split() -> None:
    page = DashboardPage()
    page.resize(1400, 900)
    page.show()
    APPLICATION.processEvents()

    chart_width, operator_width = page.workspace_splitter.sizes()
    ratio = chart_width / (chart_width + operator_width)

    assert 0.65 <= ratio <= 0.75
    assert (
        page.chart_panel.sizePolicy().horizontalPolicy()
        is QSizePolicy.Policy.Expanding
    )
    assert (
        page.operator_workspace.sizePolicy().horizontalPolicy()
        is QSizePolicy.Policy.Ignored
    )
    assert page.workspace_splitter.childrenCollapsible() is False
    page.close()
