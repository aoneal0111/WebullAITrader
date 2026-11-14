"""Mission Control's position summary must not starve the scanner on Windows."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
import pytest

from app.gui.pages.dashboard import DashboardPage
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.models import PositionsSnapshot, PositionManagementRow
from tests.gui.test_workstation_populated_state import _candidate
from app.gui.models import WatchlistSnapshot


@pytest.mark.parametrize('point_size',[10,12,14])
def test_compact_summary_preserves_scanner_and_essential_position_fields(point_size):
    app=QApplication.instance() or QApplication([])
    dashboard=DashboardPage();workspace=dashboard.market_workspace
    panel=workspace.positions_panel
    row=PositionManagementRow('PMI','LONG','400','$4.50','$4.72','+$88','+4.9%','+$60',
        '10:42:31',current_stop='$4.60',next_target='$4.90',strategy='Warrior Momentum',
        management_state='Stop active')
    try:
        dashboard.setFont(QFont(app.font().family(),point_size))
        dashboard.resize(1536,1024)
        workspace.render(WatchlistSnapshot(rows=(_candidate(),),candidate_count=1,scanner_status='Active'))
        panel.select_symbol('PMI')
        panel.render(PositionsSnapshot((('PMI','LONG','400','$4.50','$4.72','+$88','+4.9%','+$60','10:42:31','$1,800','$1,888'),),(row,)))
        dashboard.show();app.processEvents()
        assert panel.compact
        assert workspace.opportunities_section.height()>=workspace.positions_section.height()
        for key in ('SIDE / QTY','AVERAGE ENTRY','UNREALIZED','REALIZED',
                    'CURRENT CANONICAL STOP','NEXT TARGET','MANAGEMENT STATE','STRATEGY / SETUP'):
            value=panel._facts[key]
            assert value.isVisible() and value.height()>=16
            assert value.mapToGlobal(value.rect().bottomLeft()).y()<=panel._management.mapToGlobal(panel._management.rect().bottomLeft()).y()
        assert panel._facts['CURRENT CANONICAL STOP'].text()=='$4.60'
        assert panel._facts['NEXT TARGET'].text()=='$4.90'
        assert panel._facts['MARK'].isHidden()
    finally:dashboard.close();dashboard.deleteLater();app.processEvents()


def test_positions_page_retains_full_details_and_compact_has_lower_minimum_height():
    app=QApplication.instance() or QApplication([])
    full=PositionsPanel();compact=PositionsPanel(compact=True)
    try:
        full.show();compact.show();app.processEvents()
        assert not full.compact and compact.compact
        assert full._facts['MARK'].isVisible() and full._facts['THESIS'].isVisible()
        assert compact._management.minimumSizeHint().height()<full._management.minimumSizeHint().height()
    finally:
        full.close();compact.close();full.deleteLater();compact.deleteLater();app.processEvents()
