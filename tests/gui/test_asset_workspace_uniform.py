import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
import pytest

from app.assets import AssetType
from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow
from app.gui.pages.asset_workspace import AssetWorkspace
from app.gui.projections.asset_workspace import crypto_view, derivative_view
from app.asset_modules.futures_ledger_view import FuturesLedgerView
from app.asset_modules.options_ledger_view import OptionsLedgerView


@pytest.fixture(scope='module')
def application():return QApplication.instance() or QApplication([])


def report():
    a=dict(work='wallet-a',label='Bollinger · Wallet A',health='RECENT_CHECKPOINT',
        state='RUNNING',container_running=True,container_state='running',ledger_present=True,
        open_positions=1,closed_trades=1,closed_lifecycle_net_usd=12.34,open_lifecycle_realized_usd=0,working_orders=1,
        fresh_ready_pairs=28,source_states={'NO_SOURCE_BUY_SIGNAL':28},
        holdings=[dict(pair='BTC/USD',amount=.5,open_rate=50000,open_date='2026-11-13T12:00:00+00:00')],
        closed=[dict(pair='ETH/USD',amount=1,open_rate=2000,close_rate=2012.34,close_profit_abs=12.34,
            close_date='2026-11-13T13:00:00+00:00',exit_reason='ROI')],
        orders=[dict(order_id='a1',symbol='BTC/USD',ft_order_side='sell',order_type='limit',
            status='open',price=51000,amount=.5,filled=0,remaining=.5,order_date='2026-11-13T14:00:00+00:00')])
    b={**deepcopy(a),'work':'wallet-b','label':'NFI · Wallet B','closed_lifecycle_net_usd':-9,
        'holdings':[],'closed':[],'orders':[],'open_positions':0,'working_orders':0}
    return dict(engines=[a,b],checked_at='2026-11-13T15:00:00+00:00')


class Reader:
    def __init__(self):self.calls=0
    def read(self):self.calls+=1;return report()


def test_crypto_wallets_do_not_merge_balances_or_invent_marks():
    a,b=report()['engines'];v=crypto_view(a,report()['checked_at'])
    m=dict(v.portfolio.metrics)
    assert m['Realized P/L']=='+$12.34' and m['Open Positions']=='1'
    assert m['Equity']==m['Cash']==m['Buying Power']==m['Unrealized P/L']=='--'
    assert v.positions.rows[0][0]=='BTC/USD' and v.positions.rows[0][4]=='--'
    assert v.order_models.orders[0].symbol=='BTC/USD'
    assert dict(crypto_view({**a,'open_lifecycle_realized_usd':7.66},None).portfolio.metrics)['Realized P/L']=='+$20.00'
    assert dict(crypto_view(b,None).portfolio.metrics)['Realized P/L']=='-$9.00'
    assert crypto_view({**a,'container_running':None},None).state=='UNVERIFIED'
    bad=crypto_view({**a,'read_error':'ValueError'},None)
    assert not bad.positions.rows and dict(bad.portfolio.metrics)['Realized P/L']=='--'


@pytest.mark.parametrize('kind',['FUTURES','OPTIONS'])
def test_saved_contract_balance_keeps_its_market_scope(kind):
    cls=FuturesLedgerView if kind=='FUTURES' else OptionsLedgerView
    saved=cls('AVAILABLE','fixture.sqlite3','2026-11-14T00:00:00+00:00','Saved simulator',cash='9000',realized_pnl='-100',
        positions=({'contract':'MES','side':'LONG','quantity':'1','entry':'6000','stop':'5990','target':'6020'},))
    view=derivative_view(saved,kind);metrics=dict(view.portfolio.metrics)
    assert metrics['Cash']=='$9,000.00' and metrics['Realized P/L']=='-$100.00'
    assert metrics['Buying Power']==metrics['Equity']=='--'
    assert view.state=='UNAVAILABLE' and view.positions.rows[0][0]=='MES'
    bad=derivative_view(replace(saved,status='INVALID'),kind)
    assert dict(bad.portfolio.metrics)['Cash']=='--' and not bad.positions.rows


def test_crypto_uses_equity_widgets_and_wallet_selection_on_every_route(application):
    reader=Reader();page=AssetWorkspace(AssetType.CRYPTO,reader=reader)
    try:
        page.last_report=reader.read();page.results.put((page.last_report,None));page.poll()
        assert page.wallet.count()==2
        assert page.view.positions.rows[0][0]=='BTC/USD'
        assert page.dashboard.market_workspace.positions_panel._table.rowCount()==1
        assert page.orders._projected_orders[0].symbol=='BTC/USD'
        page.wallet.setCurrentIndex(1)
        assert page.view.positions.rows==()
        assert dict(page.view.portfolio.metrics)['Realized P/L']=='-$9.00'
        for route in range(10):
            page.set_route(route)
            assert page.pages.currentIndex()==route
            assert page.footer is page.dashboard.market_workspace.portfolio_section
        controls=page.dashboard.market_workspace.runtime_controls
        assert not controls.start_button.isEnabled() and not controls.stop_button.isEnabled()
    finally:page.close();page.footer.deleteLater();page.status_bar.deleteLater();page.deleteLater()


def test_all_markets_keep_totals_visible_without_starting_runtime(application,tmp_path,monkeypatch):
    from app.strategy_lab import crypto_monitor
    monkeypatch.setattr(crypto_monitor,'CryptoMonitorReader',Reader)
    composition=create_desktop_composition(paper_persistence_path=tmp_path/'equity.sqlite3')
    window=MainWindow(composition.bus,composition.state_store,composition.runtime_service,
        composition.trading_service,composition.order_command_factory,
        settings=QSettings(str(tmp_path/'uniform.ini'),QSettings.Format.IniFormat))
    try:
        window.resize(1600,1000);window.show();application.processEvents()
        equity_footer=window.dashboard.market_workspace.portfolio_section
        for asset in (AssetType.EQUITY,AssetType.CRYPTO,AssetType.FUTURES,AssetType.OPTIONS):
            window.asset_navigation.tabs.setCurrentIndex(window.asset_navigation.assets.index(asset))
            for route in (0,1,2,4,5,6,7,8,9):
                window.pages.setCurrentIndex(route);application.processEvents()
                expected=equity_footer if asset is AssetType.EQUITY else window._market_pages[asset].footer
                assert window.account_totals.currentWidget() is expected
                assert expected.isVisible() and window.account_totals.height()>40
                assert window.market_status.isVisible()
                assert not composition.runtime_service.is_active
        window.asset_navigation.tabs.setCurrentIndex(0);application.processEvents()
        assert window.account_totals.currentWidget() is equity_footer
    finally:window.close();application.processEvents();composition.close()


def test_crypto_price_precision_is_preserved_in_shared_orders_and_positions(application):
    from app.gui.projections.asset_workspace import price
    assert price('.00002')=='$0.00002'
    data=report();a=data['engines'][0]
    a['holdings'][0].update(pair='SHIB/USD',open_rate=.00002,stop_loss=.000019)
    a['orders'][0].update(symbol='SHIB/USD',price=.00003)
    page=AssetWorkspace(AssetType.CRYPTO,reader=Reader())
    try:
        page.results.put((data,None));page.poll()
        assert page.view.positions.management[0].average_entry=='$0.00002'
        assert page.view.positions.management[0].current_stop=='$0.000019'
        assert page.orders._orders_table.item(0,6).text()=='0.00003'
    finally:page.close();page.footer.deleteLater();page.status_bar.deleteLater();page.deleteLater()
