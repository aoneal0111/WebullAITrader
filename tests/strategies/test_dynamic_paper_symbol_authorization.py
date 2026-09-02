from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.configuration import PaperSymbolAuthorizationMode
from app.configuration.loader import load_configuration
from app.momentum_scanner.models import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    ScannerObservation,
)
from app.paper_trading.command_composition import (
    create_paper_trading_command_composition,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge,
)
from app.strategies.warrior_momentum.forward_models import (
    CaptureRecordType,
    FloatProvenance,
    PaperAccountContext,
    PointInTimeObservation,
)
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.forward_runtime import (
    WarriorForwardCaptureService,
)
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.models import MinuteBar
from app.strategies.warrior_momentum.risk import size_position
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime


def _bar(index: int, open_: str, high: str, low: str, close: str, volume="100"):
    at = datetime(2026, 8, 10, 14, 30, tzinfo=UTC) + timedelta(minutes=index)
    return MinuteBar("XYZ", at, *(Decimal(value) for value in (
        open_, high, low, close, volume,
    )))


def _entry_ready_observation() -> PointInTimeObservation:
    at = datetime(2026, 8, 10, 14, 50, tzinfo=UTC)
    market = ScannerObservation(
        "XYZ", at, Decimal("10.20"), Decimal("8"), Decimal("1000000"),
        Decimal("100000"), Decimal("6000000"), Decimal("10.18"),
        Decimal("10.22"), CatalystType.EARNINGS, "earnings", True, False,
        AssetClass.STOCK, CatalystStatus.TRUE,
    )
    return PointInTimeObservation(
        market,
        "REGULAR",
        (
            _bar(0, "9.7", "9.9", "9.6", "9.8"),
            _bar(1, "9.8", "10", "9.75", "9.9"),
            _bar(2, "9.9", "9.99", "9.8", "9.92"),
            _bar(3, "9.92", "10", "9.85", "9.95"),
            _bar(4, "9.96", "10.2", "9.94", "10.10", "300"),
        ),
        float_provenance=FloatProvenance.MARKET_CAP_PRICE_PROXY,
        quote_observed_at=at,
        quote_freshness_seconds=Decimal("0"),
        last_price_observed_at=at,
        last_price_freshness_seconds=Decimal("0"),
    )


def test_configuration_defaults_to_restrictive_static_allowlist():
    assert (
        load_configuration({}).paper_symbol_authorization_mode
        is PaperSymbolAuthorizationMode.STATIC_ALLOWLIST
    )


def test_dynamic_configuration_is_explicit_non_live_and_fail_closed():
    configured = load_configuration({
        "PAPER_SYMBOL_AUTHORIZATION_MODE": "dynamic_warrior",
        "WEBULL_TRADING_ENVIRONMENT": "TEST",
        "LIVE_TRADING_ENABLED": "false",
    })
    assert configured.paper_symbol_authorization_mode is PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR
    assert configured.live_trading_enabled is False
    assert configured.allowed_symbols == ()

    for invalid in (
        {"PAPER_SYMBOL_AUTHORIZATION_MODE": "anything_goes"},
        {"PAPER_SYMBOL_AUTHORIZATION_MODE": "DYNAMIC_WARRIOR", "LIVE_TRADING_ENABLED": "true"},
        {"PAPER_SYMBOL_AUTHORIZATION_MODE": "DYNAMIC_WARRIOR", "WEBULL_TRADING_ENVIRONMENT": "PRODUCTION"},
    ):
        with pytest.raises(ValueError):
            load_configuration(invalid)


def test_dynamic_mode_authorizes_only_the_authoritative_warrior_service_path(tmp_path):
    store = ForwardCaptureStore(tmp_path / "dynamic.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    composition = create_paper_trading_command_composition()
    bridge = composition.trading_service
    paper_bridge = AutonomousPaperExecutionBridge(
        bridge,
        composition.order_command_factory,
        order_book=composition.order_book,
    )
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=paper_bridge.submit_entry_decision,
    )
    try:
        _, signal = service.observe(
            _entry_ready_observation(),
            account=PaperAccountContext(
                Decimal("50000"),
                Decimal("25000"),
                frozenset({"AAPL"}),
                symbol_authorization_mode=PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR,
            ),
        )
        writer.flush()
        assert signal is not None and signal.symbol == "XYZ"
        assert len(composition.order_book.open_orders()) == 1
        decision = store.records(
            record_type=CaptureRecordType.EXECUTION_GATE_DECISION,
        )[0].payload
        assert decision["result"] == "AUTHORIZED"
        assert decision["symbol_authorization_mode"] == "DYNAMIC_WARRIOR"
        assert decision["symbol_authorization_source"] == "DYNAMIC_WARRIOR_PAPER"
    finally:
        writer.close()
        composition.close()


@pytest.mark.parametrize(
    ("account_changes", "reason"),
    (
        ({"risk_engine_approved": False}, "RISK_REJECTED"),
        ({"buying_power": Decimal("1")}, "BUYING_POWER_INSUFFICIENT"),
        ({"broker_restriction": True}, "BROKER_RESTRICTED"),
        ({"existing_exposure": Decimal("1000"), "exposure_limit": Decimal("1000")}, "EXPOSURE_LIMIT"),
    ),
)
def test_dynamic_symbol_pass_never_bypasses_downstream_account_or_risk_gates(
    tmp_path, account_changes, reason,
):
    store = ForwardCaptureStore(tmp_path / f"{reason}.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    submissions: list[object] = []
    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_entry_submitter=lambda *args: submissions.append(args) or True,
    )
    values = {
        "equity": Decimal("50000"),
        "buying_power": Decimal("25000"),
        "allowed_symbols": frozenset({"AAPL"}),
        "symbol_authorization_mode": PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR,
        **account_changes,
    }
    try:
        _, signal = service.observe(
            _entry_ready_observation(), account=PaperAccountContext(**values),
        )
        writer.flush()
        assert signal is None
        assert submissions == []
        decision = store.records(
            record_type=CaptureRecordType.EXECUTION_GATE_DECISION,
        )[0].payload
        assert decision["final_reason"] == reason
        assert decision["symbol_authorization_source"] == "DYNAMIC_WARRIOR_PAPER"
    finally:
        writer.close()


def test_direct_risk_sizing_does_not_self_authorize_a_non_allowlisted_symbol():
    observation = _entry_ready_observation()
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(
        observation.observation, observation.bars, session=observation.session,
    )
    _, signal = runtime.assess_entry(candidate)
    assert signal is not None

    position = size_position(
        signal,
        account_equity=Decimal("50000"),
        buying_power=Decimal("25000"),
        allowed_symbols=frozenset({"AAPL"}),
    )

    assert not position.approved


def test_static_mode_still_blocks_non_allowlisted_warrior_symbol(tmp_path):
    store = ForwardCaptureStore(tmp_path / "static.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    submissions: list[object] = []
    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_entry_submitter=lambda *args: submissions.append(args) or True,
    )
    try:
        _, signal = service.observe(
            _entry_ready_observation(),
            account=PaperAccountContext(
                Decimal("50000"), Decimal("25000"), frozenset({"AAPL"}),
            ),
        )
        writer.flush()
        assert signal is None
        assert submissions == []
        decision = store.records(
            record_type=CaptureRecordType.EXECUTION_GATE_DECISION,
        )[0].payload
        assert decision["final_reason"] == "SYMBOL_NOT_ALLOWED"
        assert decision["symbol_authorization_mode"] == "STATIC_ALLOWLIST"
        assert decision["symbol_authorization_source"] == "NONE"
    finally:
        writer.close()


def test_research_scanner_and_gui_packages_have_no_authorization_dependency():
    from pathlib import Path

    for root in (
        Path("app/opportunity_discovery"),
        Path("app/opportunity_learning"),
        Path("app/gui"),
        Path("app/momentum_scanner"),
    ):
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in root.rglob("*.py")
        )
        assert "PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR" not in text
        assert "symbol_authorized=" not in text
