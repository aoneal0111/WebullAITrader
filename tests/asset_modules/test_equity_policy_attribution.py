from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.equity_policy_attribution import EquityPolicyAttribution, source_fingerprint
from app.asset_modules.learning_outcomes import read_equity_outcomes
from app.asset_modules.grok_learning import LearningJournal
from app.strategies.warrior_momentum.models import SetupType
from tests.asset_modules.test_controlled_paper_service import setup, fill
from tests.asset_modules.test_native_entry_preparation import signal
from tests.asset_modules.test_desktop_crypto_allocation import desktop as _desktop


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    import tests.asset_modules.test_desktop_crypto_allocation as fixtures
    load = fixtures.load_configuration
    monkeypatch.setattr(fixtures, "load_configuration", lambda values: replace(
        load(values), historical_entry_experiment_path=tmp_path / "entry-experiment.sqlite3"))
    yield from _desktop.__wrapped__(tmp_path, monkeypatch)


def provider(config="a"*64):
    return EquityPolicyAttribution({EngineId.WARRIOR.value: config, EngineId.SCALPER.value: "b"*64})


def test_version_ignores_trade_prices_symbols_time_and_changes_with_family_configuration():
    p = provider()
    a = signal(EngineId.WARRIOR)
    a.setup_type = SetupType.HIGH_OF_DAY_BREAKOUT
    original = p(a)
    assert original["strategy_policy_version"].startswith("EQUITY_POLICY_V1:")
    a.symbol = "QQQ"; a.entry_trigger = D(100); a.lifecycle_id = "different-trade"
    assert p(a) == original
    a.setup_type = SetupType.FLAT_TOP_BREAKOUT
    assert p(a)["strategy_policy_version"] != original["strategy_policy_version"]
    a.setup_type = SetupType.HIGH_OF_DAY_BREAKOUT
    assert provider("c"*64)(a)["strategy_policy_version"] != original["strategy_policy_version"]
    assert provider()(signal(EngineId.SCALPER))["strategy_family"] == "QUICK_SCALP_MOMENTUM"


def test_no_primary_family_no_frozen_version_and_taxonomy_primary_preferred():
    a = signal(EngineId.WARRIOR)
    assert provider()(a)["strategy_family"] == "UNKNOWN"
    assert provider()(a)["strategy_policy_version"] == "UNKNOWN"
    a.setup_type = SetupType.HIGH_OF_DAY_BREAKOUT
    a.taxonomy_strategy_id = "DEEP_PULLBACK_RECLAIM"
    assert provider()(a)["strategy_family"] == "DEEP_PULLBACK_RECLAIM"


def test_source_fingerprint_normalizes_windows_newlines_and_detects_code_changes(tmp_path):
    f = tmp_path / "policy.py"
    f.write_bytes(b"line1\nline2\n")
    original = source_fingerprint(tmp_path, ["policy.py"])
    f.write_bytes(b"line1\r\nline2\r\n")
    assert source_fingerprint(tmp_path, ["policy.py"]) == original
    f.write_bytes(b"changed\n")
    assert source_fingerprint(tmp_path, ["policy.py"]) != original


def test_missing_sources_are_explicit_unknown(tmp_path):
    a = signal(EngineId.WARRIOR); a.setup_type = SetupType.BULL_FLAG
    p = EquityPolicyAttribution({EngineId.WARRIOR.value: "a"*64}, source_root=tmp_path)
    assert p(a)["strategy_code_fingerprint"] == p(a)["strategy_policy_version"] == "UNKNOWN"


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_native_preparation_controller_durable_orders_and_protection_keep_original_version(setup, engine):
    commands, bridge, service, *_ = setup
    bridge.strategy_provenance_source = provider()
    a = signal(engine); a.setup_type = SetupType.HIGH_OF_DAY_BREAKOUT
    prepared = bridge.prepare_entry_decision(a, 10, D(1))
    expected = dict(prepared.placement.order.metadata)
    assert expected["strategy_policy_version"].startswith("EQUITY_POLICY_V1:")
    result = service.place_order(prepared.placement)
    assert result.success and bridge.adopt_entry_acknowledgement(prepared.placement, result)
    entry = commands.order_book.get(result.broker_order_id)
    assert entry.request.metadata["strategy_policy_version"] == expected["strategy_policy_version"]
    assert entry.request.metadata["policy_version"] != expected["strategy_policy_version"]
    fill(commands)
    bridge.strategy_provenance_source = provider("c"*64)
    bridge.reconcile_protection()
    sells = [o for o in commands.order_book.history() if o.side.value == "SELL"]
    assert sells
    assert all(o.request.metadata["strategy_policy_version"] == expected["strategy_policy_version"] for o in sells)
    restored = commands.durable_store.orders()
    assert any(o.request.metadata.get("strategy_family") == expected["strategy_family"] for o in restored)


def test_attribution_callback_failure_does_not_change_preparation_gates(setup):
    _, bridge, *_ = setup
    def broken(_):
        raise OSError("source unavailable")
    bridge.strategy_provenance_source = broken
    a = signal(EngineId.WARRIOR)
    result = bridge.prepare_entry_decision(a, 10, D(1))
    assert result.placement is not None
    assert "strategy_policy_version" not in result.placement.order.metadata


def test_old_learning_outcomes_reimport_without_rewriting_payload(tmp_path):
    from tests.asset_modules.test_learning_outcomes import ledger, order
    path = ledger(tmp_path, [order("BUY", "10", "100"), order("SELL", "10", "102")])
    old = read_equity_outcomes(path)["outcomes"]
    assert "strategy_family" not in old[0]
    journal = LearningJournal(tmp_path / "learning.sqlite3")
    journal.ingest_outcomes(old)
    assert journal.ingest_outcomes(read_equity_outcomes(path)["outcomes"])["inserted"] == 0
    assert journal.outcome_feedback()["groups"][0]["strategy_family"] == "UNKNOWN"


def test_new_tagged_outcomes_group_by_family_and_policy(tmp_path):
    from tests.asset_modules.test_learning_outcomes import ledger, order
    orders = [order("BUY", "10", "100"), order("SELL", "10", "102")]
    orders[0]["request"]["metadata"].update(strategy_policy_version="frozen-v1", strategy_family="BULL_FLAG")
    path = ledger(tmp_path, orders)
    r = read_equity_outcomes(path)["outcomes"]
    assert r[0]["strategy_family"] == "BULL_FLAG"
    journal = LearningJournal(tmp_path / "learning.sqlite3"); journal.ingest_outcomes(r)
    assert journal.outcome_feedback()["groups"][0]["policy_version"] == "frozen-v1"
    assert journal.outcome_feedback()["groups"][0]["strategy_family"] == "BULL_FLAG"


def test_desktop_installs_both_engine_attribution_sources_without_api_calls(desktop):
    _, composition, *_ = desktop
    bridge = composition.autonomous_paper_bridge
    assert isinstance(bridge.strategy_provenance_source, EquityPolicyAttribution)
    for engine in (EngineId.WARRIOR, EngineId.SCALPER):
        a = signal(engine); a.setup_type = SetupType.HIGH_OF_DAY_BREAKOUT
        assert bridge.strategy_provenance_source(a)["strategy_policy_version"].startswith("EQUITY_POLICY_V1:")


def test_replacement_keeps_original_version_after_provider_change():
    from tests.strategies.test_autonomous_paper_bridge import Signal, create_paper_trading_command_composition
    from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
    commands = create_paper_trading_command_composition()
    try:
        bridge = AutonomousPaperExecutionBridge(commands.trading_service, commands.order_command_factory,
                                                order_book=commands.order_book)
        tag_signal = signal(EngineId.WARRIOR); tag_signal.setup_type = SetupType.BULL_FLAG
        original = provider()(tag_signal)
        bridge.strategy_provenance_source = lambda _: original
        assert bridge.submit_entry(Signal(), 1000, D(1000))
        bridge.strategy_provenance_source = provider("c"*64)
        result = bridge.replace_entry_order(lifecycle_id="trade-a", replacement_limit=D("10.5"),
            structural_stop=D("9.5"), original_risk_budget=D(1000), account_equity=D(10000),
            buying_power=D(10000), revalidate=lambda: True, reason="attribution-test")
        assert result.submitted
        replacement = commands.order_book.get(result.replacement_order_id)
        assert replacement.request.metadata["strategy_policy_version"] == original["strategy_policy_version"]
    finally:
        commands.close()


def test_addon_keeps_original_version_after_provider_change():
    from tests.strategies.test_autonomous_paper_bridge import (
        Signal, create_paper_trading_command_composition, session_timestamp, MarketEvent,
        MarketEventType, QuotePayload,
    )
    from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
    commands = create_paper_trading_command_composition(position_quantity_source=lambda _: D(100))
    try:
        bridge = AutonomousPaperExecutionBridge(commands.trading_service, commands.order_command_factory,
                                                order_book=commands.order_book)
        a = signal(EngineId.WARRIOR); a.setup_type = SetupType.BULL_FLAG
        original = provider()(a)
        bridge.strategy_provenance_source = lambda _: original
        assert bridge.submit_entry(Signal(), 100, D(50))
        commands.gateway.process_market_event(MarketEvent(1, session_timestamp(1), "PMI", "test",
            MarketEventType.QUOTE, QuotePayload(D("9.99"), D(10), D(100), D(100))))
        bridge.strategy_provenance_source = provider("c"*64)
        result = bridge.submit_add_on(Signal(entry_trigger=D("10.1")), 25, D("2.5"),
            parent_lifecycle_id="trade-a", add_on_id="one-addon")
        assert result.authorized
        addon = commands.order_book.history()[-1]
        assert addon.request.metadata["strategy_policy_version"] == original["strategy_policy_version"]
    finally:
        commands.close()


def test_dispatch_does_not_read_policy_sources(monkeypatch):
    from pathlib import Path
    p = provider()
    a = signal(EngineId.WARRIOR); a.setup_type = SetupType.BULL_FLAG
    expected = p(a)
    def forbid_read(_):
        raise AssertionError("Per-trade disk read")
    monkeypatch.setattr(Path, "read_bytes", forbid_read)
    assert p(a) == expected


def test_scalper_stream_and_bar_setups_have_separate_policy_versions():
    p = provider()
    a = signal(EngineId.SCALPER)
    a.setup_type = 'QUICK_SCALP_STREAM'
    stream = p(a)
    a.setup_type = 'QUICK_SCALP_MOMENTUM'
    bar = p(a)
    assert stream['strategy_family'] == 'QUICK_SCALP_STREAM'
    assert stream['strategy_policy_version'] != bar['strategy_policy_version']
