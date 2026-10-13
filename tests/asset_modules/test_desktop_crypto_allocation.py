from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.engine_catalog import EngineId
from app.assets import AssetType
from app.composition.asset_modules import create_asset_modules
from app.composition.crypto_allocation import validate_crypto_allocation
from app.configuration.loader import load_configuration
from tests.asset_modules.test_futures_allocation import equity
from tests.asset_modules.test_supervisor import market, proposal
from tests.asset_modules.test_paper_controller_router import setup, entry, NOW


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    import app.composition.desktop as module
    import app.configuration as configuration_module
    import app.composition.asset_modules as markets
    config = load_configuration({'WEBULL_TRADING_ENVIRONMENT': 'PAPER',
        'ATLAS_PAPER_CONTROLLER_ENABLED': 'true',
        'ATLAS_CRYPTO_SHARED_ALLOCATION_ENABLED': 'true',
        'EXECUTION_DATABASE_PATH': str(tmp_path/'execution.db'),
        'WARRIOR_FORWARD_CAPTURE_PATH': str(tmp_path/'capture.db'),
        'CRYPTO_DISCOVERY_PATH': str(tmp_path/'research.jsonl')})
    monkeypatch.setattr(module, 'load_configuration', lambda: config)
    monkeypatch.setattr(configuration_module, 'load_configuration', lambda: config)
    monkeypatch.setattr(markets, 'configured_suite', lambda: None)
    composition = module.create_desktop_composition(paper_persistence_path=tmp_path/'equity.db')
    opened = []
    def modules():
        result = create_asset_modules(composition)
        opened.append(result)
        return result
    try:
        yield config, composition, modules, tmp_path
    finally:
        for result in opened:
            result.crypto_supervisor.close()
            # Tests may explicitly close a ledger before reopening.
            try: result.crypto_supervisor.paper.close()
            except Exception: pass
        composition.close()


def test_flag_defaults_off():
    assert not load_configuration({}).crypto_shared_allocation_enabled


@pytest.mark.parametrize('change', ['live', 'controller', 'service', 'environment'])
def test_invalid_composition_refuses_before_ledger_creation(desktop, change):
    config, composition, _, root = desktop
    if change == 'live': config = replace(config, live_trading_enabled=True)
    elif change == 'controller': config = replace(config, paper_controller_enabled=False)
    elif change == 'environment':
        from app.configuration.models import TradingEnvironment
        config = replace(config, environment=TradingEnvironment.TEST)
    else: composition.paper_controller_service = None
    with pytest.raises(ValueError, match='PAPER controller'):
        validate_crypto_allocation(config, composition)
    assert not (root/'crypto-paper.sqlite3').exists()


def test_real_desktop_uses_same_controller_and_never_activates(desktop):
    _, composition, make, _ = desktop
    modules = make()
    supervisor = modules.crypto_supervisor
    eq = composition.paper_controller_service.router
    assert supervisor.entry_router.controller is eq.controller
    assert supervisor.entry_router.account_id == eq.account_id
    assert not supervisor.entries_enabled and not supervisor.entry_router.enabled
    assert supervisor.thread is None and supervisor.provider is None
    assert not composition.runtime_service.is_active
    assert eq.controller.snapshot(eq.account_id)['active_commands'] == 0
    assert modules.status(AssetType.CRYPTO) == 'OFF'


def test_equity_reservation_blocks_crypto_then_final_exit_releases(desktop):
    _, composition, make, _ = desktop
    supervisor = make().crypto_supervisor
    eq = composition.paper_controller_service.router
    shared = eq.controller
    row = market()
    supervisor.source = lambda: [row]
    supervisor.provider = SimpleNamespace(propose=lambda context: [proposal()])
    supervisor.entries_enabled = True
    now = datetime.now(UTC)
    capital = shared.allocation_balance(eq.account_id)['configured_capital']
    request = replace(equity(capital=capital), account_id=eq.account_id,
        quote_at=now, expires_at=now+timedelta(seconds=30))
    assert shared.reserve(request, now=now).state == 'RESERVED'
    # Reserved but unsubmitted equity commands are also an uncertainty gate.
    supervisor.tick()
    assert supervisor.status == 'ENTRY_NOT_AUTHORIZED'
    shared.abandon_unsubmitted('equity', EngineId.WARRIOR)
    supervisor.next_request = 0
    row.timestamp = datetime.now(UTC)
    supervisor.tick()
    assert supervisor.status == 'PAPER FILLED'
    assert shared.snapshot(eq.account_id)['reserved_capital'] > D(200)
    supervisor.entries_enabled = False
    row.bid, row.ask, row.timestamp = D(110), D('110.1'), datetime.now(UTC)
    supervisor.tick()
    assert not supervisor.paper.snapshot()['positions']
    assert shared.snapshot(eq.account_id)['active_commands'] == 0
    assert shared.allocation_balance(eq.account_id)['crypto_realized_pnl'] > 0


def test_restart_recovers_same_crypto_and_campaign_binding(desktop):
    _, composition, make, _ = desktop
    first = make().crypto_supervisor
    row = market()
    first.source = lambda: [row]
    first.provider = SimpleNamespace(propose=lambda context: [proposal()])
    first.entries_enabled = True
    first.tick()
    assert first.status == 'PAPER FILLED'
    identity = first.paper.ledger_id
    assert first.close()
    first.paper.close()
    second = make().crypto_supervisor
    assert second.paper.ledger_id == identity
    assert len(second.paper.snapshot()['positions']) == 1
    assert not second.entries_enabled and not second.entry_router.enabled
    assert set(second.entry_router.reconcile().values()) == {'HELD'}
    assert second.entry_router.controller is composition.paper_controller_service.router.controller


def test_legacy_crypto_position_is_not_adopted_or_deleted(desktop):
    _, _, make, root = desktop
    paper = CryptoPaper(root/'crypto-paper.sqlite3')
    row = market()
    paper.apply({**proposal(), 'id': 'legacy'}, {'BTC/USD': row})
    paper.close()
    with pytest.raises(ValueError, match='existing crypto exposure'): make()
    reopened = CryptoPaper(root/'crypto-paper.sqlite3')
    try:
        assert len(reopened.snapshot()['positions']) == 1
        assert reopened.intent_evidence('legacy')['state'] == 'OPEN'
    finally: reopened.close()


def test_campaign_failure_refuses_buys_but_native_protection_survives(desktop, monkeypatch):
    _, composition, make, _ = desktop
    supervisor = make().crypto_supervisor
    row = market()
    supervisor.source = lambda: [row]
    supervisor.provider = SimpleNamespace(propose=lambda context: [proposal()])
    supervisor.entries_enabled = True
    def stale_campaign(): raise ValueError('Controller campaign changed')
    original = composition.paper_controller_service.router.shared_allocation_ready
    monkeypatch.setattr(composition.paper_controller_service.router, 'shared_allocation_ready', stale_campaign)
    supervisor.tick()
    assert supervisor.status == 'ENTRY_NOT_AUTHORIZED'
    assert not supervisor.paper.snapshot()['positions']
    monkeypatch.setattr(composition.paper_controller_service.router, 'shared_allocation_ready', original)
    supervisor.next_request = 0
    row.timestamp = datetime.now(UTC)
    supervisor.tick()
    assert supervisor.status == 'PAPER FILLED'
    monkeypatch.setattr(composition.paper_controller_service.router, 'shared_allocation_ready', stale_campaign)
    supervisor.entries_enabled = False
    row.bid, row.ask, row.timestamp = D(110), D('110.1'), datetime.now(UTC)
    supervisor.tick()
    assert not supervisor.paper.snapshot()['positions']
    assert supervisor.entry_router.controller.snapshot(supervisor.entry_router.account_id)['active_commands'] == 0


@pytest.mark.parametrize('state', ['held', 'reserved', 'uncertain', 'unmanaged'])
def test_shared_equity_readiness_checks_authoritative_exposure(setup, state):
    shared, gateway, router = setup
    assert router.shared_allocation_ready()
    if state in {'held', 'unmanaged'}:
        assert router.submit(entry()).state == 'ACKNOWLEDGED'
        if state == 'unmanaged':
            order = gateway.order_book.history()[0]
            gateway.order_book.update(replace(order, request=replace(order.request,
                strategy_lifecycle_id='legacy-unmanaged')))
    else:
        shared.reserve(entry(), now=NOW)
        if state == 'uncertain': shared.claim_dispatch('one', EngineId.WARRIOR, now=NOW)
    assert router.shared_allocation_ready() is (state == 'held')


def test_operator_close_reconciles_shared_pool_immediately(desktop):
    from PySide6.QtWidgets import QApplication, QLabel
    from app.gui.pages.crypto_paper import CryptoPaperPage
    app = QApplication.instance() or QApplication([])
    _, _, make, _ = desktop
    supervisor = make().crypto_supervisor
    row = market()
    supervisor.source = lambda: [row]
    supervisor.provider = SimpleNamespace(propose=lambda context: [proposal()])
    supervisor.entries_enabled = True
    supervisor.tick()
    page = CryptoPaperPage('Mission Control', supervisor)
    try:
        assert any('Shared campaign allocation' in label.text() for label in page.findChildren(QLabel))
        row.timestamp = datetime.now(UTC)
        page.close_paper_positions()
        assert not supervisor.entries_enabled and not supervisor.entry_router.enabled
        assert not supervisor.paper.snapshot()['positions']
        assert supervisor.entry_router.controller.snapshot(supervisor.entry_router.account_id)['active_commands'] == 0
    finally:
        page.timer.stop()
        page.close()
        page.deleteLater()
        app.processEvents()
