"""Opt-in provider composition, shared client and fresh runtime ownership."""
from dataclasses import replace

import pytest

from app.composition.grok_paper import create_grok_paper_suite, bind_grok_paper_entries
from app.composition.runtime_mode import RuntimeMode
from app.configuration.loader import load_configuration
from tests.asset_modules.test_controlled_paper_service import setup, NOW
from tests.asset_modules.test_grok_desktop_producer import desktop, producer


def config(tmp_path, **values):
    return load_configuration({'WEBULL_TRADING_ENVIRONMENT': 'PAPER',
        'ATLAS_PAPER_CONTROLLER_ENABLED': 'true', 'QUICK_SCALPER_ENABLED': 'true',
        'WARRIOR_FORWARD_PAPER_ENABLED': 'true', 'ATLAS_GROK_PAPER_ENTRIES_ENABLED': 'true',
        'EXECUTION_DATABASE_PATH': str(tmp_path / 'execution.db'),
        'WARRIOR_FORWARD_CAPTURE_PATH': str(tmp_path / 'capture.db'),
        'CRYPTO_DISCOVERY_PATH': str(tmp_path / 'crypto.jsonl'), **values})


def credentials(monkeypatch):
    monkeypatch.setenv('XAI_API_KEY', 'test-private-key')
    monkeypatch.setenv('ATLAS_GROK_MODEL', 'grok-test')


def test_default_configuration_never_constructs_provider(monkeypatch):
    import app.composition.grok_paper as module
    monkeypatch.setattr(module, 'configured_client', lambda *_: pytest.fail('Disabled provider constructed'))
    c = load_configuration({})
    assert not c.grok_paper_entries_enabled
    assert create_grok_paper_suite(c, RuntimeMode.PAPER) is None


@pytest.mark.parametrize('change', ['environment', 'live', 'controller', 'engines', 'runtime'])
def test_invalid_entry_configuration_rejected_before_provider(tmp_path, monkeypatch, change):
    import app.composition.grok_paper as module
    from app.configuration.models import TradingEnvironment
    c, mode = config(tmp_path), RuntimeMode.PAPER
    if change == 'environment':
        c = replace(c, environment=TradingEnvironment.TEST)
    elif change == 'live':
        c = replace(c, live_trading_enabled=True)
    elif change == 'controller':
        c = replace(c, paper_controller_enabled=False)
    elif change == 'engines':
        c = replace(c, quick_scalper_enabled=False, warrior_forward_paper_enabled=False)
    else:
        mode = RuntimeMode.SIMULATED
    monkeypatch.setattr(module, 'configured_client', lambda *_: pytest.fail('Invalid provider constructed'))
    with pytest.raises(ValueError, match='Grok entries require PAPER'):
        create_grok_paper_suite(c, mode)


@pytest.mark.parametrize('missing', ['key', 'model', 'bad_model'])
def test_missing_provider_settings_never_fall_back_or_expose_values(tmp_path, monkeypatch, missing):
    credentials(monkeypatch)
    if missing == 'key':
        monkeypatch.delenv('XAI_API_KEY')
    elif missing == 'model':
        monkeypatch.delenv('ATLAS_GROK_MODEL')
    else:
        monkeypatch.setenv('ATLAS_GROK_MODEL', 'invalid-test-private-key')
    with pytest.raises(ValueError, match='Configure .*XAI_API_KEY') as caught:
        create_grok_paper_suite(config(tmp_path), RuntimeMode.PAPER)
    assert 'test-private-key' not in str(caught.value)


def test_valid_suite_constructs_zero_network_requests(tmp_path, monkeypatch):
    credentials(monkeypatch)
    ai = create_grok_paper_suite(config(tmp_path), RuntimeMode.PAPER, clock=lambda: NOW)
    assert ai.client.requests == 0 and not ai._latest
    assert ai.crypto.client is ai.client
    assert all(a.client is ai.client for a in ai.engines.values())
    assert ai.master.client is ai.client


def test_real_desktop_composition_shares_suite_with_crypto_without_activation(tmp_path, monkeypatch):
    import app.composition.desktop as module
    import app.composition.asset_modules as markets
    import app.configuration as configuration_module
    c = config(tmp_path)
    credentials(monkeypatch)
    monkeypatch.setattr(module, 'load_configuration', lambda: c)
    monkeypatch.setattr(configuration_module, 'load_configuration', lambda: c)
    monkeypatch.setattr(markets, 'configured_suite', lambda: pytest.fail('Second provider constructed'))
    composition = module.create_desktop_composition(paper_persistence_path=tmp_path / 'paper.db', paper_clock=lambda: NOW)
    modules = markets.create_asset_modules(composition)
    try:
        assert composition.grok_suite is modules.grok_suite
        assert modules.crypto_supervisor.provider is composition.grok_suite.crypto
        assert composition.grok_suite.client.requests == 0
        sidecar = composition.warrior_forward_sidecar
        assert sidecar._grok_native_producer.review.handoff.worker.dispatcher.service is composition.paper_controller_service
        assert not sidecar.grok_native_execution_permitted()
        assert not composition.runtime_service.is_active
        assert modules.crypto_supervisor.thread is None
        assert not composition.paper_order_book.history()
    finally:
        modules.crypto_supervisor.close()
        modules.crypto_supervisor.paper.close()
        composition.close()


def test_factory_restarts_fresh_native_review_and_retains_shared_provider(setup, tmp_path):
    p, ai, _ = producer(setup)
    sidecar, observer = desktop(setup, tmp_path, p)
    # Replace only after the original binding has safely closed.
    sidecar.stop()
    bind_grok_paper_entries(sidecar, ai, setup[2], setup[1], clock=lambda: NOW)
    first = sidecar._grok_native_producer
    try:
        sidecar.start('PAPER')
        assert sidecar.grok_native_execution_permitted()
        sidecar.stop()
        assert first.review._closed
        sidecar.start('PAPER')
        second = sidecar._grok_native_producer
        assert second is not first and not second.review._closed
        assert sidecar._service._paper_entry_reviewer == second.warrior_offer
        assert observer._paper_entry_reviewer == second.scalper_offer
        assert ai.client.requests == 0
        with pytest.raises(ValueError, match='stopped'):
            bind_grok_paper_entries(sidecar, ai, setup[2], setup[1], clock=lambda: NOW)
    finally:
        sidecar.stop()

@pytest.mark.parametrize('enabled', [False, True])
def test_offline_check_reports_settings_without_request_or_secret(tmp_path, monkeypatch, capsys, enabled):
    import json
    from app.asset_modules import grok_configuration_check as module
    credentials(monkeypatch)
    c = config(tmp_path, ATLAS_GROK_PAPER_ENTRIES_ENABLED=str(enabled).lower())
    monkeypatch.setattr(module, 'load_configuration', lambda: c)
    monkeypatch.setattr(module, 'load_dotenv', lambda **_: None)
    assert module.main() == 0
    text = capsys.readouterr().out
    result = json.loads(text)
    assert 'test-private-key' not in text
    assert result['requests'] == 0 and result['provider_constructible']
    assert not result['credentials_validated_online']
    assert result['entry_composition_configured'] is enabled
