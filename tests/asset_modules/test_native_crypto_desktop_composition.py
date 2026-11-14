from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.asset_modules.crypto_native_observer import NativeCryptoObserver
from app.assets import AssetType
from app.configuration import load_configuration
from tests.asset_modules.test_desktop_crypto_allocation import desktop


def config_for(monkeypatch,config,**changes):
    chosen=replace(config,crypto_native_observer_enabled=True,**changes)
    monkeypatch.setattr('app.configuration.load_configuration',lambda:chosen)
    return chosen


def test_native_flag_is_explicit_and_default_off():
    assert not load_configuration({}).crypto_native_observer_enabled
    assert load_configuration({'ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED':'true'}).crypto_native_observer_enabled


@pytest.mark.parametrize('case',['shared','grok','runtime'])
def test_invalid_native_configuration_fails_before_opening_ledger(desktop,monkeypatch,case):
    config,composition,make,root=desktop
    changes={}
    if case=='shared':changes['crypto_shared_allocation_enabled']=False
    elif case=='grok':changes['grok_paper_entries_enabled']=True
    else:composition.crypto_research_runtime=None
    config_for(monkeypatch,config,**changes)
    with pytest.raises(ValueError,match='Native crypto observer'):make()
    assert not (root/'crypto-paper.sqlite3').exists()


def test_desktop_selects_exactly_one_native_owner_without_model_or_start(desktop,monkeypatch):
    config,composition,make,root=desktop
    config_for(monkeypatch,config)
    calls=[]
    class Model:
        def propose(self,*args):calls.append(True);raise AssertionError('No model call')
    composition.grok_suite=SimpleNamespace(crypto=Model())
    supervisor=make().crypto_supervisor
    assert isinstance(supervisor,NativeCryptoObserver)
    assert supervisor.provider is None and supervisor.thread is None
    assert not supervisor.entries_enabled and not supervisor.entry_router.enabled
    assert not calls and not composition.runtime_service.is_active


def test_module_start_stop_owns_worker_without_starting_equity_or_data_network(desktop,monkeypatch):
    config,composition,make,root=desktop
    config_for(monkeypatch,config)
    lifecycle=[]
    composition.optional_research=SimpleNamespace(start=lambda:lifecycle.append('START'),
                                                close=lambda:lifecycle.append('STOP') or True)
    modules=make();observer=modules.crypto_supervisor
    try:
        modules.activate(AssetType.CRYPTO)
        assert observer.thread is not None and observer.protection_thread is not None
        assert modules.status(AssetType.CRYPTO)=='ACTIVE'
        assert not composition.runtime_service.is_active
        assert observer.metrics()['entry_authority'] is False
        modules.deactivate(AssetType.CRYPTO)
        assert observer.thread is None and observer.protection_thread is None
        assert modules.status(AssetType.CRYPTO)=='OFF'
        assert lifecycle==['START','STOP']
    finally:observer.close()


def test_crypto_page_never_offers_native_entry_or_requests_grok_key(desktop,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from app.gui.pages.crypto_paper import CryptoPaperPage
    app=QApplication.instance() or QApplication([])
    config,composition,make,root=desktop
    config_for(monkeypatch,config)
    supervisor=make().crypto_supervisor
    page=CryptoPaperPage('Mission Control',supervisor)
    try:
        page.show();app.processEvents();page.refresh()
        assert not page.enabled.isEnabled() and not page.enabled.isChecked()
        assert 'Native strategy observation' in page.enabled.text()
        assert 'XAI_API_KEY' not in page.state.text()
        assert 'entries disabled' in page.state.text()
        assert supervisor.thread is None
    finally:
        page.timer.stop();page.close();page.deleteLater();app.processEvents()
