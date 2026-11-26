import time
from PySide6.QtWidgets import QApplication
from app.assets import AssetType
from app.gui.pages.asset_workspace import AssetWorkspace
from tests.gui.test_asset_workspace_uniform import Reader


def test_buttons_control_selected_crypto_worker_and_report_verified_stop():
    app=QApplication.instance() or QApplication([])
    from copy import deepcopy
    from tests.gui.test_asset_workspace_uniform import report
    calls=[];data=report()
    class MutableReader:
        def read(self):return deepcopy(data)
    class Control:
        def execute(self,action,work):
            calls.append((action,work))
            for bot in data['engines']:
                if bot['work']==work or action=='emergency':bot['container_running']=action=='start'
            return dict(success=True,results=[dict(work=work,success=True,state='RUNNING' if action=='start' else 'STOPPED')])
    page=AssetWorkspace(AssetType.CRYPTO,reader=MutableReader(),controller=Control())
    try:
        page.last_report=page.reader.read();page.results.put((page.last_report,None));page.poll()
        controls=page.dashboard.market_workspace.runtime_controls
        assert controls.stop_button.isEnabled() and not controls.start_button.isEnabled()
        controls.stop_button.click()
        assert page.command_busy and not controls.stop_button.isEnabled()
        deadline=time.monotonic()+2
        while page.command_busy and time.monotonic()<deadline:
            page.poll();app.processEvents()
        assert calls==[('stop','wallet-a')]
        assert 'verified' in page.control_message.text()
        assert controls.start_button.isEnabled()
        controls.start_button.click()
        deadline=time.monotonic()+2
        while (page.command_busy or page.busy) and time.monotonic()<deadline:
            page.poll();app.processEvents()
        assert calls[-1]==('start','wallet-a') and controls.stop_button.isEnabled()
        page.wallet.setCurrentIndex(1)
        controls.emergency_stop_button.click()
        deadline=time.monotonic()+2
        while page.command_busy and time.monotonic()<deadline:
            page.poll();app.processEvents()
        assert calls[-1]==('emergency','wallet-b')
    finally:
        page.close();page.footer.deleteLater();page.status_bar.deleteLater();page.deleteLater();app.processEvents()


def test_native_scanner_and_position_details_survive_equity_workspace_projection():
    from tests.gui.test_asset_workspace_uniform import report
    app=QApplication.instance() or QApplication([])
    data=report();bot=data['engines'][0];bot['holdings'][0].update(id=1,stop_loss=49000)
    bot['workspace_telemetry']=dict(account=dict(currency='USD',total=7000,available_stake=6900),
        positions=[dict(id=1,pair='BTC/USD',amount=.5,mark=51000,mark_at='2026-11-13T12:00:00+00:00',
                        mark_basis='CANDLE CLOSE ESTIMATE',market_value=25500,unrealized=480,profit_ratio=.0192)],
        markets={'BTC/USD':dict(ready=True,timeframe='4h',candle_at='2026-11-13T12:00:00+00:00',
            candle_close=51000,candle_volume=10000,source=dict(state='SOURCE_BUY_SIGNAL',buy=1,
                entry_inputs={'rsi':22},entry_parameters={'WIDTH':2.5}))})
    page=AssetWorkspace(AssetType.CRYPTO,reader=Reader())
    try:
        page.results.put((data,None));page.poll()
        ws=page.dashboard.market_workspace
        assert ws.watchlist._table.rowCount()==1
        assert ws.positions_panel._facts['CURRENT CANONICAL STOP'].text()=='$49,000.00'
        assert ws.positions_panel._facts['UNREALIZED'].text()=='+$480.00  +1.92%'
        assert ws.trade_intelligence._header_metrics['Warrior momentum'].text()==bot['label']
        assert ws.trade_intelligence._decision.text()=='SOURCE BUY SIGNAL'
        assert 'rsi' in ws.trade_intelligence._reason.text() and 'WIDTH' in ws.trade_intelligence._reason.text()
        ws.watchlist._view_buttons['Qualifying'].click()
        assert ws.watchlist._table.rowCount()==1
        ws.watchlist._view_buttons['Near Miss'].click()
        assert ws.watchlist._table.rowCount()==0
    finally:
        page.close();page.footer.deleteLater();page.status_bar.deleteLater();page.deleteLater();app.processEvents()


def test_failed_stop_remains_visible_and_never_claims_stopped():
    app=QApplication.instance() or QApplication([])
    class Control:
        def execute(self,*args):return dict(success=False,results=[dict(error='DOCKER_STOP_FAILED')])
    page=AssetWorkspace(AssetType.CRYPTO,reader=Reader(),controller=Control())
    try:
        page.results.put((page.reader.read(),None));page.poll()
        page.dashboard.market_workspace.runtime_controls.stop_button.click()
        deadline=time.monotonic()+2
        while page.command_busy and time.monotonic()<deadline:
            page.poll();app.processEvents()
        assert 'CRYPTO COMMAND FAILED' in page.control_message.text()
        assert 'DOCKER_STOP_FAILED' in page.control_message.text()
        assert page.view.state=='RUNNING'
    finally:
        page.close();page.footer.deleteLater();page.status_bar.deleteLater();page.deleteLater();app.processEvents()
