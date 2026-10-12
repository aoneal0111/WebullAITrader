from dataclasses import replace
from hashlib import sha256
import sqlite3
from datetime import timedelta
from decimal import Decimal as D

import pytest
from PySide6.QtWidgets import QApplication

from app.assets import AssetType
from app.asset_modules.options_ledger_view import OptionsLedgerReader
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.options_signals import propose
from app.gui.pages.market_analysis import DerivativeWorkspace
from tests.asset_modules.test_options_signals import NOW, SETTINGS, data


@pytest.fixture
def saved(tmp_path):
    path = tmp_path/'options.sqlite3'
    ledger = OptionsPaper(path); ledger.set_enabled(True)
    h,uq,cs,qs = data()
    proposals,_ = propose(h,uq,cs,qs,NOW,SETTINGS)
    p = proposals[0]; c = p.plan.contract; q = qs[c.symbol]
    assert ledger.enter(p.intent_id,p.plan,q,now=NOW)=='OPENED'
    try: yield path,ledger,c,q,p
    finally: ledger.close()


def test_read_only_saved_balances_do_not_double_count_paid_premium(saved):
    path,ledger,c,q,p = saved
    before = sha256(path.read_bytes()).hexdigest()
    view = OptionsLedgerReader(path).read()
    assert view.status=='AVAILABLE' and ledger.enabled
    assert D(view.cash)==D('1693.4') and view.realized_pnl=='0'
    assert D(view.open_premium_cost)==D(804) and D(view.reserved_risk)==D('21.2')
    assert D(view.exit_fee_reserve)==D('2.6') and D(view.cash_headroom)==D('1690.8')
    assert view.positions[0]['contract']==c.symbol and view.positions[0]['right']=='CALL'
    assert view.positions[0]['quantity']=='4' and view.events[0]['action']=='BUY'
    assert view.events[0]['net_pnl'] is None and 'live quotes' in view.notice
    assert sha256(path.read_bytes()).hexdigest()==before
    later = NOW+timedelta(seconds=1)
    closing = replace(q,bid=p.plan.target,ask=p.plan.target+c.tick,source_at=later,observed_at=later)
    ledger.protect({c.symbol:closing},now=later)
    closed = OptionsLedgerReader(path).read()
    assert closed.positions==() and D(closed.realized_pnl)==D('26.8')
    assert closed.events[0]['action']=='SELL' and D(closed.events[0]['net_pnl'])==D('26.8')


def test_missing_or_unselected_ledger_never_creates_file(tmp_path):
    assert OptionsLedgerReader().read().status=='UNCONFIGURED'
    path = tmp_path/'missing.sqlite3'
    reader = OptionsLedgerReader(path)
    assert reader.read().status=='NOT_FOUND' and not path.exists()
    assert reader.read().cash is None


def test_foreign_and_futures_ledgers_cannot_be_shown_as_options(tmp_path):
    from app.asset_modules.futures_paper import FuturesPaper
    path = tmp_path/'foreign.sqlite3'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE positions (symbol TEXT)')
    before = path.read_bytes()
    assert OptionsLedgerReader(path).read().status=='INVALID' and path.read_bytes()==before
    path.write_bytes(b'not sqlite')
    assert OptionsLedgerReader(path).read().status=='INVALID'
    futures = FuturesPaper(tmp_path/'futures.sqlite3')
    try: assert OptionsLedgerReader(tmp_path/'futures.sqlite3').read().status=='INVALID'
    finally: futures.close()


@pytest.mark.parametrize('corruption', ['bad_account','wrong_underlying','too_many','oversized',
    'bad_event','bad_quantity','wrong_engine','bad_multiplier','oversized_event'])
def test_invalid_saved_data_does_not_claim_flat_or_profitable(saved,corruption):
    path,ledger,c,q,p = saved
    if corruption=='bad_account': ledger.db.execute("UPDATE account SET cash='NaN'")
    if corruption=='wrong_underlying': ledger.db.execute("UPDATE positions SET underlying='AAPL'")
    if corruption=='too_many':
        for name in ('X','Y'):
            ledger.db.execute('INSERT INTO positions SELECT ?,?,plan,entry_fill,opened_at,last_quote_at,reserved_risk FROM positions LIMIT 1',(name,name))
    if corruption=='oversized': ledger.db.execute('UPDATE positions SET plan=?',(' '*16385,))
    if corruption=='bad_event': ledger.db.execute('UPDATE journal SET payload=?',('{"secret":"NEVER_DISPLAY_THIS"}',))
    if corruption=='bad_quantity': ledger.db.execute("UPDATE positions SET plan=json_set(plan,'$.quantity',1.5)")
    if corruption=='wrong_engine': ledger.db.execute("UPDATE positions SET plan=json_set(plan,'$.contract.engine','FUTURES')")
    if corruption=='bad_multiplier': ledger.db.execute("UPDATE positions SET plan=json_set(plan,'$.contract.multiplier','10')")
    if corruption=='oversized_event': ledger.db.execute('UPDATE journal SET payload=?',(' '*16385,))
    view = OptionsLedgerReader(path).read()
    assert view.status=='INVALID' and view.cash is None and view.positions==() and view.events==()
    assert 'unknown' in view.notice and 'NEVER_DISPLAY_THIS' not in view.notice


def test_busy_ledger_means_inventory_unknown(saved):
    path,ledger,*_ = saved
    blocker = sqlite3.connect(path,isolation_level=None)
    try:
        blocker.execute('BEGIN EXCLUSIVE')
        view = OptionsLedgerReader(path).read()
        assert view.status=='BUSY' and view.cash is None and 'unknown' in view.notice
    finally: blocker.close()
    assert OptionsLedgerReader(path).read().status=='AVAILABLE'


def test_events_bounded_and_not_inventory(saved):
    path,ledger,c,*_ = saved
    for i in range(60):
        with ledger.transaction():
            ledger._event(NOW+timedelta(seconds=i+1),intent_id=f'extra-{i}',symbol=c.symbol,
                action='BUY',quantity=1,fill=D(2))
    view = OptionsLedgerReader(path).read()
    assert view.status=='AVAILABLE' and len(view.events)==50
    assert view.events[0]['intent']=='extra-59' and len(view.positions)==1


def test_widget_saved_positions_events_and_expiry_calculator(saved):
    app = QApplication.instance() or QApplication([])
    path,ledger,c,q,p = saved
    reader = OptionsLedgerReader(path)
    page = DerivativeWorkspace(AssetType.OPTIONS,'Positions',ledger_reader=reader)
    events = DerivativeWorkspace(AssetType.OPTIONS,'Orders',ledger_reader=reader)
    try:
        assert page.content.rowCount()==1 and events.content.rowCount()==1
        assert page.content.item(0,0).text()==c.symbol and page.content.item(0,2).text()=='CALL'
        assert events.content.item(0,2).text()=='BUY'
        assert 'Open premium cost (already paid)' in page.ledger_summary.text()
        assert 'No options feed or worker' in page.status.text() and ledger.enabled
        for key,value in dict(strike='100',premium='2',underlying='90',multiplier='100',contracts='2',fees='1').items():
            page.inputs[key].setText(value)
        page.calculate.click()
        assert '$-402.00' in page.result.text()
        reader.select(path.with_name('absent.sqlite3')); page.refresh_ledger.click()
        assert page.content.rowCount()==0 and 'unknown' in page.ledger_summary.text()
        assert not reader.path.exists() and ledger.enabled
    finally: page.close(); events.close()


def test_desktop_options_routes_share_reader_without_starting_market(saved):
    app = QApplication.instance() or QApplication([])
    from app.asset_modules.lifecycle import AssetModules
    from app.composition.desktop import create_desktop_composition
    from app.gui.main_window import MainWindow
    path,ledger,c,*_ = saved
    composition = create_desktop_composition(paper_persistence_path=path.with_name('equity.sqlite3'))
    modules = AssetModules({}); modules.options_ledger_reader = OptionsLedgerReader(path)
    window = MainWindow(composition.bus,composition.state_store,composition.runtime_service,
        composition.trading_service,composition.order_command_factory,asset_modules=modules)
    try:
        window.show(); app.processEvents()
        window.asset_navigation.tabs.setCurrentIndex(window.asset_navigation.assets.index(AssetType.OPTIONS)); app.processEvents()
        positions = window.asset_surface.currentWidget()
        assert positions.ledger_reader is modules.options_ledger_reader and positions.content.rowCount()==1
        window.pages.setCurrentIndex(2); app.processEvents()
        events = window.asset_surface.currentWidget()
        assert events.ledger_reader is positions.ledger_reader and events.content.item(0,2).text()=='BUY'
        window.asset_navigation.tabs.setCurrentIndex(window.asset_navigation.assets.index(AssetType.FUTURES)); app.processEvents()
        assert window.asset_surface.currentWidget().ledger_reader is not positions.ledger_reader
        assert window.asset_surface.currentWidget().content.rowCount()==0
        assert modules.status(AssetType.OPTIONS)=='NOT AVAILABLE'
        assert not composition.runtime_service.is_active and ledger.enabled
    finally: window.close(); composition.close()
