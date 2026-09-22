import pytest
from PySide6.QtWidgets import QApplication
from app.assets import AssetType
from app.configuration import load_configuration
from app.composition.desktop import create_desktop_composition
from app.composition.asset_modules import create_asset_modules
from app.gui.main_window import MainWindow


def test_composed_crypto_is_idle_until_activated_and_restarts(monkeypatch, tmp_path):
    import app.composition.desktop as desktop
    import app.configuration as configuration_module
    config = load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'EXECUTION_DATABASE_PATH':str(tmp_path/'execution.sqlite3'),
        'CRYPTO_DISCOVERY_PATH':str(tmp_path/'crypto.jsonl')})
    monkeypatch.setattr(desktop, 'load_configuration', lambda: config)
    monkeypatch.setattr(configuration_module, 'load_configuration', lambda: config)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    composition = create_desktop_composition(paper_persistence_path=tmp_path/'equity.sqlite3')
    # No network in tests. This uses the real worker lifecycle with an injected absent provider.
    composition.crypto_research_runtime._provider = None
    modules = create_asset_modules(composition)
    try:
        assert modules.status(AssetType.EQUITY) == 'OFF'
        assert modules.status(AssetType.CRYPTO) == 'OFF'
        assert modules.status(AssetType.FUTURES) == 'NOT AVAILABLE'
        assert modules.crypto_supervisor.provider is None
        for _ in range(2):
            modules.activate(AssetType.CRYPTO)
            assert modules.status(AssetType.CRYPTO) == 'ACTIVE'
            assert not composition.runtime_service.is_active
            modules.deactivate(AssetType.CRYPTO)
            assert modules.status(AssetType.CRYPTO) == 'OFF'
        modules.set_budget(1)
        modules.activate(AssetType.CRYPTO)
        with pytest.raises(ValueError):
            modules.activate(AssetType.EQUITY)
    finally:
        modules.crypto_supervisor.close()
        modules.crypto_supervisor.paper.close()
        composition.close()


def test_real_crypto_pages_do_not_show_equity_rows(monkeypatch, tmp_path):
    from app.asset_modules.crypto_paper import CryptoPaper
    from app.asset_modules.supervisor import CryptoSupervisor
    from app.asset_modules.lifecycle import AssetModules
    app = QApplication.instance() or QApplication([])
    composition = create_desktop_composition(paper_persistence_path=tmp_path/'equity.sqlite3')
    modules = AssetModules({})
    modules.crypto_supervisor = CryptoSupervisor(CryptoPaper(tmp_path/'crypto.sqlite3'), lambda:())
    window = MainWindow(composition.bus, composition.state_store,
                        composition.runtime_service, composition.trading_service,
                        composition.order_command_factory, asset_modules=modules)
    try:
        window.show()
        window.asset_navigation.tabs.setCurrentIndex(1)
        app.processEvents()
        page = window.asset_surface.currentWidget()
        assert page.supervisor is modules.crypto_supervisor
        assert not page.enabled.isEnabled()
        assert page.content.rowCount() == 0
        assert not window.global_status.isVisible()
        window.asset_navigation.tabs.setCurrentIndex(0)
        app.processEvents()
        assert window.asset_surface.currentWidget() is window.pages
        assert window.global_status.isVisible()
    finally:
        window.close()
        modules.crypto_supervisor.paper.close()
        composition.close()
