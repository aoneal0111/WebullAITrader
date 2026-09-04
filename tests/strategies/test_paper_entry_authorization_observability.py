from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.order_placement import OrderPlacementDecision
from tests.test_support.session_clock import (
    create_session_paper_composition as create_paper_trading_command_composition,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge,
    PaperEntryAuthorizationReason,
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
import app.strategies.warrior_momentum.forward_runtime as forward_runtime_module
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.models import MinuteBar
from app.momentum_scanner.models import (
    AssetClass,
    CatalystStatus,
    CatalystType,
    ScannerObservation,
)


PAPER_AT = datetime(2026, 8, 10, 14, 50, tzinfo=UTC)


@dataclass(frozen=True)
class Signal:
    symbol: str = "XYZ"
    entry_trigger: Decimal = Decimal("10")
    lifecycle_id: str = "entry-ready-1"


class _FactoryFailure:
    def create_placement_request(self, _command):
        raise ValueError("deliberate")


class _PlacementService:
    def __init__(self, decision: OrderPlacementDecision):
        self.decision = decision
        self.calls = 0

    def place_order(self, _request):
        self.calls += 1
        return SimpleNamespace(
            success=self.decision is OrderPlacementDecision.SUCCESS,
            decision=self.decision,
            broker_order_id="paper-1" if self.decision is OrderPlacementDecision.SUCCESS else "",
        )


class _Factory:
    def create_placement_request(self, command):
        return command


def _bridge(*, decision=OrderPlacementDecision.SUCCESS, **changes):
    service = _PlacementService(decision)
    values = dict(
        trading_service=service,
        order_command_factory=_Factory(),
        order_book=None,
    )
    values.update(changes)
    return AutonomousPaperExecutionBridge(**values), service


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"enabled": False}, PaperEntryAuthorizationReason.PAPER_DISABLED),
        ({"mode": "LIVE"}, PaperEntryAuthorizationReason.ENVIRONMENT_MISMATCH),
    ),
)
def test_enablement_and_environment_refusals_are_explicit(changes, reason):
    bridge, service = _bridge(**changes)
    decision = bridge.submit_entry_decision(Signal(), 100, Decimal("50"))
    assert not decision.authorized and decision.reason is reason
    assert not decision.order_constructed and not decision.submission_attempted
    assert service.calls == 0


def test_reconciliation_readiness_refusal_is_explicit():
    bridge, service = _bridge()
    bridge.begin_reconciliation()
    decision = bridge.submit_entry_decision(Signal(), 100, Decimal("50"))
    assert decision.reason is PaperEntryAuthorizationReason.BROKER_NOT_READY
    assert service.calls == 0


def test_working_order_and_position_refusals_are_distinct():
    composition = create_paper_trading_command_composition(at=PAPER_AT)
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
    )
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        decision = bridge.submit_entry_decision(Signal(), 100, Decimal("50"))
        assert decision.reason is PaperEntryAuthorizationReason.WORKING_ORDER_EXISTS
    finally:
        composition.close()

    positioned, service = _bridge()
    positioned._active_by_symbol["XYZ"] = "authoritative-position"
    decision = positioned.submit_entry_decision(Signal(), 100, Decimal("50"))
    assert decision.reason is PaperEntryAuthorizationReason.POSITION_EXISTS
    assert service.calls == 0


def test_invalid_quantity_and_order_construction_failure_are_explicit():
    bridge, service = _bridge()
    invalid = bridge.submit_entry_decision(Signal(), 0, Decimal("50"))
    assert invalid.reason is PaperEntryAuthorizationReason.INVALID_QUANTITY
    assert service.calls == 0

    broken = AutonomousPaperExecutionBridge(_PlacementService(
        OrderPlacementDecision.SUCCESS,
    ), _FactoryFailure(), order_book=None)
    decision = broken.submit_entry_decision(Signal(), 100, Decimal("50"))
    assert decision.reason is PaperEntryAuthorizationReason.ORDER_CONSTRUCTION_FAILED
    assert not decision.submission_attempted


@pytest.mark.parametrize(
    ("placement", "reason"),
    (
        (OrderPlacementDecision.DISABLED, PaperEntryAuthorizationReason.ORDER_PLACEMENT_DISABLED),
        (OrderPlacementDecision.SESSION_INVALID, PaperEntryAuthorizationReason.SESSION_BLOCKED),
        (OrderPlacementDecision.GATEWAY_FAILURE, PaperEntryAuthorizationReason.GATEWAY_FAILURE),
        (OrderPlacementDecision.ORDER_REJECTED, PaperEntryAuthorizationReason.ORDER_REJECTED),
    ),
)
def test_placement_runtime_refusals_retain_exact_reason(placement, reason):
    bridge, service = _bridge(decision=placement)
    decision = bridge.submit_entry_decision(Signal(), 100, Decimal("50"))
    assert decision.reason is reason
    assert decision.order_constructed and decision.submission_attempted
    assert decision.placement_decision == placement.value
    assert service.calls == 1


def _bar(index: int, open_: str, high: str, low: str, close: str, volume="100"):
    at = datetime(2026, 8, 10, 14, 30, tzinfo=UTC) + timedelta(minutes=index)
    return MinuteBar("XYZ", at, *(Decimal(value) for value in (
        open_, high, low, close, volume,
    )))


def _observation():
    at = datetime(2026, 8, 10, 14, 50, tzinfo=UTC)
    market = ScannerObservation(
        "XYZ", at, Decimal("10.20"), Decimal("8"), Decimal("1000000"),
        Decimal("100000"), Decimal("6000000"), Decimal("10.18"),
        Decimal("10.22"), CatalystType.EARNINGS, "earnings", True, False,
        AssetClass.STOCK, CatalystStatus.TRUE,
    )
    bars = (
        _bar(0, "9.7", "9.9", "9.6", "9.8"),
        _bar(1, "9.8", "10", "9.75", "9.9"),
        _bar(2, "9.9", "9.99", "9.8", "9.92"),
        _bar(3, "9.92", "10", "9.85", "9.95"),
        _bar(4, "9.96", "10.2", "9.94", "10.10", "300"),
    )
    return PointInTimeObservation(
        market, "REGULAR", bars,
        float_provenance=FloatProvenance.MARKET_CAP_PRICE_PROXY,
        quote_observed_at=at, quote_freshness_seconds=Decimal("0"),
        last_price_observed_at=at, last_price_freshness_seconds=Decimal("0"),
    )


def test_authorization_result_is_appended_without_changing_submission(tmp_path):
    store = ForwardCaptureStore(tmp_path / "capture.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    composition = create_paper_trading_command_composition(at=PAPER_AT)
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
    )
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=bridge.submit_entry_decision,
    )
    try:
        _, signal = service.observe(
            _observation(),
            account=PaperAccountContext(
                Decimal("50000"), Decimal("25000"), frozenset({"XYZ"}),
            ),
        )
        writer.flush()
        assert signal is not None
        records = store.records(
            record_type=CaptureRecordType.EXECUTION_GATE_DECISION,
        )
        assert len(records) == 1
        assert records[0].payload["result"] == "AUTHORIZED"
        assert records[0].payload["submission_attempted"] is True
        assert len(composition.order_book.open_orders()) == 1
    finally:
        writer.close()
        composition.close()


def test_allowlist_refusal_retains_the_exact_failed_gate(tmp_path):
    store = ForwardCaptureStore(tmp_path / "allowlist.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    submitted = []
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: submitted.append(True) or True,
    )
    try:
        _, signal = service.observe(
            _observation(),
            account=PaperAccountContext(
                Decimal("50000"), Decimal("25000"), frozenset({"AAPL"}),
            ),
        )
        writer.flush()
        assert signal is None
        blocked = tuple(
            record.payload
            for record in store.records(record_type=CaptureRecordType.STATE_TRANSITION)
            if record.payload.get("to") == "ENTRY_BLOCKED"
        )
        assert blocked[-1]["reason_codes"] == ["EXECUTION_NOT_ALLOWED"]
        assert blocked[-1]["blocking_gates"] == [{
            "gate": "paper_symbol_authorization",
            "limit": "STATIC_ALLOWLIST",
            "observed": "NONE",
            "passed": False,
        }]
        decisions = store.records(
            record_type=CaptureRecordType.EXECUTION_GATE_DECISION,
        )
        assert len(decisions) == 1
        assert decisions[0].payload["final_reason"] == "SYMBOL_NOT_ALLOWED"
        assert decisions[0].payload["result"] == "REFUSED"
        assert not submitted
    finally:
        writer.close()


def test_diagnostic_serialization_failure_cannot_change_authorization(
    tmp_path, monkeypatch,
):
    store = ForwardCaptureStore(tmp_path / "isolated.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    composition = create_paper_trading_command_composition(at=PAPER_AT)
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
    )
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=bridge.submit_entry_decision,
    )
    monkeypatch.setattr(
        forward_runtime_module,
        "_execution_gate_record",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("diagnostic failure")),
    )
    try:
        _, signal = service.observe(
            _observation(),
            account=PaperAccountContext(
                Decimal("50000"), Decimal("25000"), frozenset({"XYZ"}),
            ),
        )
        assert signal is not None
        assert len(composition.order_book.open_orders()) == 1
    finally:
        writer.close()
        composition.close()
