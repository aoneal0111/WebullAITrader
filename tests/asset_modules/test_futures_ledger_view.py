from dataclasses import replace
from hashlib import sha256
import sqlite3
from datetime import timedelta
from decimal import Decimal as D

import pytest
from PySide6.QtWidgets import QApplication

from app.assets import AssetType
from app.asset_modules.futures_ledger_view import FuturesLedgerReader
from app.asset_modules.futures_paper import FuturesPaper
from app.asset_modules.futures_signals import propose
from app.gui.pages.market_analysis import DerivativeWorkspace
from tests.asset_modules.test_futures_signals import NOW, SETTINGS, data


@pytest.fixture
def saved(tmp_path):
    path = tmp_path/'futures.sqlite3'
    ledger = FuturesPaper(path); ledger.set_enabled(True)
    c,h,q = data()
    proposal,_ = propose(c,h,q,NOW,SETTINGS)
    assert ledger.enter(proposal.intent_id,proposal.plan,q,now=NOW)=='OPENED'
    try: yield path,ledger,c,q,proposal
    finally: ledger.close()


def test_read_only_account_positions_and_events_are_not_runtime_or_broker_state(saved):
    path,ledger,c,q,p = saved
    before = sha256(path.read_bytes()).hexdigest()
    view = FuturesLedgerReader(path).read()
    assert view.status=='AVAILABLE' and ledger.enabled
    assert view.cash=='9998' and view.realized_pnl=='0'
    assert D(view.reserved_margin)==D(2000) and D(view.reserved_risk)==D(19)
    assert D(view.margin_headroom)==D(7996)
    assert view.positions[0]['contract']==c.symbol
    assert view.positions[0]['quantity']=='2' and view.events[0]['action']=='OPEN'
    assert view.events[0]['net_pnl'] is None
    assert 'live quotes' in view.notice
    assert sha256(path.read_bytes()).hexdigest()==before
    later = NOW+timedelta(seconds=1)
    closing = replace(q,bid=p.plan.target,ask=p.plan.target+c.tick,source_at=later,observed_at=later)
    ledger.protect({c.symbol:closing},now=later)
    closed = FuturesLedgerReader(path).read()
    assert closed.positions==() and D(closed.realized_pnl)==D(21)
    assert closed.events[0]['action']=='CLOSE' and D(closed.events[0]['net_pnl'])==D(21)


def test_missing_or_unselected_ledger_never_creates_a_file(tmp_path):
    assert FuturesLedgerReader().read().status=='UNCONFIGURED'
    path = tmp_path/'missing.sqlite3'
    reader = FuturesLedgerReader(path)
    assert reader.read().status=='NOT_FOUND' and not path.exists()
    assert reader.read().cash is None


def test_foreign_sqlite_and_invalid_bytes_are_not_inferred_as_futures(tmp_path):
    path = tmp_path/'foreign.sqlite3'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE positions (symbol TEXT)')
    before = path.read_bytes()
    assert FuturesLedgerReader(path).read().status=='INVALID'
    assert path.read_bytes()==before
    path.write_bytes(b'not sqlite')
    assert FuturesLedgerReader(path).read().status=='INVALID'


@pytest.mark.parametrize('corruption', ['bad_account','wrong_symbol','too_many','oversized','bad_event','bad_quantity'])
def test_invalid_saved_data_is_unavailable_not_flat_or_profitable(saved,corruption):
    path,ledger,c,q,p = saved
    if corruption=='bad_account': ledger.db.execute("UPDATE account SET cash='NaN'")
    if corruption=='wrong_symbol': ledger.db.execute("UPDATE positions SET symbol='EQUITY_SYMBOL'")
    if corruption=='too_many':
        for name in ('X','Y'):
            ledger.db.execute('INSERT INTO positions SELECT ?,?,plan,entry_fill,opened_at,last_quote_at,reserved_risk,reserved_margin FROM positions LIMIT 1',(name,name))
    if corruption=='oversized': ledger.db.execute('UPDATE positions SET plan=?',(' '*16385,))
    if corruption=='bad_event': ledger.db.execute('UPDATE journal SET payload=?',('{"secret":"NEVER_DISPLAY_THIS"}',))
    if corruption=='bad_quantity': ledger.db.execute("UPDATE positions SET plan=json_set(plan,'$.quantity',1.5)")
    view = FuturesLedgerReader(path).read()
    assert view.status=='INVALID' and view.cash is None
    assert view.positions==() and view.events==()
    assert 'unknown' in view.notice and 'NEVER_DISPLAY_THIS' not in view.notice


def test_busy_ledger_does_not_claim_an_empty_position_snapshot(saved):
    path,ledger,*_ = saved
    blocker = sqlite3.connect(path,isolation_level=None)
    try:
        blocker.execute('BEGIN EXCLUSIVE')
        view = FuturesLedgerReader(path).read()
        assert view.status=='BUSY' and view.cash is None and 'unknown' in view.notice
    finally: blocker.close()
    assert FuturesLedgerReader(path).read().status=='AVAILABLE'


def test_events_are_bounded_and_do_not_define_inventory(saved):
    path,ledger,c,*_ = saved
    for i in range(60):
        with ledger.transaction():
            ledger._event(NOW+timedelta(seconds=i+1),intent_id=f'extra-{i}',symbol=c.symbol,
                action='OPEN',side='LONG',quantity=1,fill=D(100))
    view = FuturesLedgerReader(path).read()
    assert view.status=='AVAILABLE' and len(view.events)==50
    assert view.events[0]['intent']=='extra-59' and len(view.positions)==1


def test_widget_displays_saved_data_keeps_scenario_and_never_changes_activation(saved):
    app = QApplication.instance() or QApplication([])
    path,ledger,c,q,p = saved
    reader = FuturesLedgerReader(path)
    page = DerivativeWorkspace(AssetType.FUTURES,'Positions',ledger_reader=reader)
    events = DerivativeWorkspace(AssetType.FUTURES,'Orders',ledger_reader=reader)
    try:
        assert page.content.rowCount()==1 and events.content.rowCount()==1
        assert page.content.item(0,0).text()==c.symbol
        assert events.content.item(0,2).text()=='OPEN'
        assert 'Saved simulator balances' in page.ledger_summary.text()
        assert 'No futures feed or worker' in page.status.text() and ledger.enabled
        for key,value in dict(entry='100',exit='101',tick_size='.25',tick_value='1.25',contracts='2',fees='1').items():
            page.inputs[key].setText(value)
        page.calculate.click()
        assert '$8.00' in page.result.text()
        reader.select(path.with_name('absent.sqlite3'))
        page.refresh_ledger.click()
        assert page.content.rowCount()==0 and 'unknown' in page.ledger_summary.text()
        assert not reader.path.exists() and ledger.enabled
    finally: page.close(); events.close()


def test_desktop_futures_routes_share_reader_but_never_start_a_market(saved):
    app = QApplication.instance() or QApplication([])
    from app.asset_modules.lifecycle import AssetModules
    from app.composition.desktop import create_desktop_composition
    from app.gui.main_window import MainWindow
    path,ledger,c,*_ = saved
    composition = create_desktop_composition(paper_persistence_path=path.with_name('equity.sqlite3'))
    modules = AssetModules({})
    modules.futures_ledger_reader = FuturesLedgerReader(path)
    window = MainWindow(composition.bus,composition.state_store,composition.runtime_service,
        composition.trading_service,composition.order_command_factory,asset_modules=modules)
    try:
        window.show(); app.processEvents()
        window.asset_navigation.tabs.setCurrentIndex(window.asset_navigation.assets.index(AssetType.FUTURES))
        app.processEvents()
        positions = window.asset_surface.currentWidget()
        assert positions.ledger_reader is modules.futures_ledger_reader and positions.content.rowCount()==1
        window.pages.setCurrentIndex(2); app.processEvents()
        events = window.asset_surface.currentWidget()
        assert events.ledger_reader is positions.ledger_reader and events.content.item(0,2).text()=='OPEN'
        window.asset_navigation.tabs.setCurrentIndex(window.asset_navigation.assets.index(AssetType.OPTIONS))
        app.processEvents()
        assert window.asset_surface.currentWidget().ledger_reader is None
        assert window.asset_surface.currentWidget().content.rowCount()==0
        assert modules.status(AssetType.FUTURES)=='NOT AVAILABLE'
        assert not composition.runtime_service.is_active and ledger.enabled
    finally: window.close(); composition.close()
