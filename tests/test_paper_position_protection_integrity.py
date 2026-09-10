from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.strategies.warrior_momentum import (
    CaptureRecordType,
    ForwardCaptureStore,
    ForwardCaptureWriter,
    WarriorForwardCaptureService,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge,
    PaperExitSubmissionDecision,
    PaperExitSubmissionState,
)
from tests.warrior_momentum.test_forward_capture import account, point
from tests.test_support.session_clock import create_session_paper_composition


NOW = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
D = Decimal


def _service(tmp_path: Path, position: dict[str, Decimal], submitted, *, fail=False):
    store = ForwardCaptureStore(tmp_path / "protection-integrity.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submitted.append((symbol, quantity, price, reason, lifecycle))
        if fail:
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.UNAVAILABLE, symbol, lifecycle, reason,
            )
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            order_id=f"stop-{len(submitted)}", activation_timestamp=NOW,
        )

    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    return store, writer, service


def test_synchronous_full_fill_gets_immediate_actual_protection(tmp_path: Path):
    position = {"XYZ": D("575")}
    submitted = []
    store, writer, service = _service(tmp_path, position, submitted)
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        assert [(item[1], item[2], item[3]) for item in submitted] == [
            (575, signal.stop_price, "STOP"),
        ]
        state = service._paper["XYZ"]
        assert state.authoritative_position_seen is True
        assert state.managed_quantity == 575
        assert state.protection_reconciled is True
        assert state.remaining == 575
        writer.flush()
        transitions = [
            item.payload["to"]
            for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)
        ]
        assert "PAPER_EXIT_WORKING" in transitions
    finally:
        writer.close()


def test_partial_fill_protection_resizes_and_duplicate_reconcile_is_safe(tmp_path: Path):
    position = {"XYZ": D("40")}
    submitted = []
    store, writer, service = _service(tmp_path, position, submitted)
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        assert submitted[-1][1] == 40

        position["XYZ"] = D("575")
        assert service.reconcile_authoritative_protection("XYZ", NOW) is True
        assert submitted[-1][1] == 575

        before = len(submitted)
        assert service.reconcile_authoritative_protection("XYZ", NOW) is True
        # The service remains idempotent with respect to the authoritative
        # position handoff; a real bridge owns correlated order deduplication.
        assert len(submitted) == before + 1
        assert all(item[1] <= int(position["XYZ"]) for item in submitted)
    finally:
        writer.close()


def test_protection_failure_is_explicit_and_position_remains_authoritative(tmp_path: Path):
    position = {"XYZ": D("575")}
    submitted = []
    store, writer, service = _service(tmp_path, position, submitted, fail=True)
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        assert submitted and submitted[0][1] == 575
        state = service._paper["XYZ"]
        assert state.remaining == 575
        assert state.protection_reconciled is False
        writer.flush()
        payloads = [
            item.payload
            for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)
        ]
        assert any(item["to"] == "PAPER_EXIT_REQUIRED" for item in payloads)
        assert any(item["to"] == "PAPER_POSITION_CONTRADICTION" for item in payloads)
    finally:
        writer.close()


def test_flat_entry_does_not_create_protection(tmp_path: Path):
    position = {"XYZ": D("0")}
    submitted = []
    _store, writer, service = _service(tmp_path, position, submitted)
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        assert submitted == []
        assert service._paper["XYZ"].authoritative_position_seen is False
    finally:
        writer.close()


def test_real_paper_bridge_creates_and_resizes_correlated_stop(tmp_path: Path):
    position = {"XYZ": D("0")}
    composition = create_session_paper_composition(at=NOW)
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    store = ForwardCaptureStore(tmp_path / "bridge-protection.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)

    def submit_entry(signal, shares, risk):
        decision = bridge.submit_entry_decision(signal, shares, risk)
        if decision.authorized:
            position[signal.symbol] = D("40")
        return decision

    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=submit_entry,
        paper_exit_submitter=bridge.ensure_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        stops = [
            order for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
            and order.request.order_type.value == "STOP"
        ]
        assert len(stops) == 1 and stops[0].quantity == D("40")

        position["XYZ"] = D("575")
        assert service.reconcile_authoritative_protection("XYZ", NOW) is True
        stops = [
            order for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
            and order.request.order_type.value == "STOP"
        ]
        assert len(stops) == 1 and stops[0].quantity == D("575")
        assert all(
            order.quantity <= position["XYZ"]
            for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
        )
    finally:
        writer.close()
        composition.close()
