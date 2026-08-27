from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, Decimal as D
from pathlib import Path
from threading import Event
from time import sleep

import pytest

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.strategies.warrior_momentum import (
    CaptureRecord, CaptureRecordType,
    FloatProvenance, ForwardCaptureWriter, ForwardCaptureStore,
    ForwardTransition, MinuteBar, PaperAccountContext,
    PointInTimeObservation, WarriorForwardCaptureService,
    build_daily_report, persist_daily_report, replay_captured_decision,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge,
)

T0 = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)


def bar(i: int, o: str, h: str, low: str, c: str, volume: str = "100") -> MinuteBar:
    return MinuteBar("XYZ", T0 + timedelta(minutes=i), D(o), D(h), D(low), D(c), D(volume))


def bars() -> tuple[MinuteBar, ...]:
    return (
        bar(0, "9.7", "9.9", "9.6", "9.8"),
        bar(1, "9.8", "10", "9.75", "9.9"),
        bar(2, "9.9", "9.99", "9.8", "9.92"),
        bar(3, "9.92", "10", "9.85", "9.95"),
        bar(4, "9.96", "10.2", "9.94", "10.10", "300"),
    )


def scanner(**changes) -> ScannerObservation:
    values = dict(
        symbol="XYZ", timestamp=T0 + timedelta(minutes=20), price=D("10.20"),
        previous_close=D("8"), current_volume=D("1000000"),
        average_30_day_volume=D("100000"), float_shares=D("6000000"),
        bid=D("10.18"), ask=D("10.22"), catalyst=CatalystType.EARNINGS,
        catalyst_headline="earnings", tradable=True, halted=False,
        asset_class=AssetClass.STOCK, catalyst_status=CatalystStatus.TRUE,
    )
    values.update(changes)
    return ScannerObservation(**values)


def point(**changes) -> PointInTimeObservation:
    values = dict(
        observation=scanner(), session="REGULAR", bars=bars(),
        float_provenance=FloatProvenance.MARKET_CAP_PRICE_PROXY,
        catalyst_event_timestamp=T0 - timedelta(hours=1),
        catalyst_source="WEBULL_EARNINGS", quote_observed_at=T0 + timedelta(minutes=20),
        quote_freshness_seconds=D("0.2"), halt_state_known=True,
    )
    values.update(changes)
    return PointInTimeObservation(**values)


def account() -> PaperAccountContext:
    return PaperAccountContext(D("50000"), D("25000"), frozenset({"XYZ"}))


@pytest.fixture
def capture(tmp_path: Path):
    store = ForwardCaptureStore(tmp_path / "forward.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    yield store, writer, service
    writer.close()


def test_point_in_time_capture_persists_evidence_and_excludes_future_bar(capture) -> None:
    store, writer, service = capture
    future = bar(30, "10", "30", "9", "29", "999999")
    value = point(bars=(*bars(), future))
    service.observe(value)
    writer.flush()
    discovery = store.records(record_type=CaptureRecordType.DISCOVERY)[0].payload
    spread = store.records(record_type=CaptureRecordType.SPREAD_EVIDENCE)[0].payload
    catalyst = store.records(record_type=CaptureRecordType.CATALYST_EVIDENCE)[0].payload
    stored_bars = store.records(record_type=CaptureRecordType.MINUTE_BAR)
    assert discovery["float_provenance"] == "MARKET_CAP_PRICE_PROXY"
    assert spread["bid"] == "10.18" and spread["ask"] == "10.22"
    assert spread["freshness_seconds"] == "0.2"
    assert catalyst["evidence_state"] == "TRUE"
    assert catalyst["event_timestamp"] == (T0 - timedelta(hours=1)).isoformat()
    assert len(stored_bars) == len(bars())
    assert all(item.payload["bar_timestamp"] != future.timestamp.isoformat() for item in stored_bars)


def test_quality_preserves_unknown_unavailable_and_missing_provenance(capture) -> None:
    store, writer, service = capture
    observation = scanner(
        bid=None, ask=None, float_shares=None, catalyst=CatalystType.NONE,
        catalyst_status=CatalystStatus.UNAVAILABLE,
    )
    service.observe(point(
        observation=observation, bars=(), float_provenance=FloatProvenance.UNKNOWN,
        quote_observed_at=None, quote_freshness_seconds=None,
        halt_state_known=False, volume_known=False, historical_bars_available=False,
    ))
    writer.flush()
    quality = store.records(record_type=CaptureRecordType.DATA_QUALITY)[0].payload
    catalyst = store.records(record_type=CaptureRecordType.CATALYST_EVIDENCE)[0].payload
    assert all(quality[key] for key in (
        "missing_bid_ask", "stale_bid_ask", "unavailable_catalyst",
        "missing_float", "missing_volume", "missing_historical_bars",
        "halt_uncertainty",
    ))
    assert catalyst["evidence_state"] == "UNAVAILABLE"


def test_transitions_blocked_diagnostics_and_counterfactual_are_separate(capture) -> None:
    store, writer, service = capture
    service.observe(point(observation=scanner(bid=D("9"), ask=D("11"))), account=account())
    writer.flush()
    transitions = [item.payload for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)]
    blocked = [item for item in transitions if item["to"] == ForwardTransition.ENTRY_BLOCKED.value]
    assert blocked
    assert "spread" in {gate["gate"] for item in blocked for gate in item["blocking_gates"]}
    counter = store.records(record_type=CaptureRecordType.COUNTERFACTUAL)
    assert counter and counter[0].payload["excluded_from_v1_performance"] is True
    assert not store.records(record_type=CaptureRecordType.PAPER_FILL)


def test_after_hours_risk_rejection_remains_blocked(capture) -> None:
    store, writer, service = capture
    rejected_account = PaperAccountContext(
        D("50000"), D("25000"), frozenset({"XYZ"}), risk_engine_approved=False,
    )

    candidate, signal = service.observe(
        point(session="AFTER_HOURS"), account=rejected_account,
    )
    writer.flush()

    assert signal is None
    assert candidate.session == "AFTER_HOURS"
    transitions = [
        item.payload
        for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)
    ]
    blocked = [item for item in transitions if item["to"] == "ENTRY_BLOCKED"]
    assert blocked
    assert any("RISK_REJECTED" in item["reason_codes"] for item in blocked)
    assert all("SESSION_NOT_ALLOWED" not in item["reason_codes"] for item in blocked)
    assert not store.records(record_type=CaptureRecordType.PAPER_FILL)


def test_after_hours_signal_reaches_normal_paper_gateway_once(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "after-hours-forward.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
    )
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=bridge.submit_entry,
    )
    try:
        candidate, signal = service.observe(
            point(session="AFTER_HOURS"), account=account(),
        )

        assert candidate.status.value == "ENTRY_READY"
        assert signal is not None and signal.session == "AFTER_HOURS"
        assert len(composition.order_book.open_orders()) == 1
        assert bridge.submit_entry(signal, 100, D("50")) is False
        assert len(composition.order_book.open_orders()) == 1

        reports = composition.gateway.process_market_event(MarketEvent(
            1, datetime.now(UTC), signal.symbol, "after-hours-test",
            MarketEventType.QUOTE,
            QuotePayload(
                signal.entry_trigger - D("0.01"), signal.entry_trigger,
                D("1000"), D("1000"),
            ),
        ))

        assert reports and reports[0].fills
        assert reports[0].fills[0].quantity > 0
        assert len(composition.order_book.history()) == 1
        assert composition.order_book.history()[0].symbol == signal.symbol
    finally:
        writer.close()
        composition.close()


def test_paper_entry_partial_exit_stop_first_and_survives_candidate_removal(capture) -> None:
    store, writer, service = capture
    _candidate, signal = service.observe(point(), account=account())
    assert signal is not None and signal.execution_authorized is False
    # No new scanner observation is supplied: the retained paper state advances directly.
    first = MinuteBar("XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
                      signal.target_levels[0], signal.entry_trigger, signal.target_levels[0], D("500"))
    service.observe_market_bar("XYZ", first, first.timestamp + timedelta(minutes=1))
    ambiguous = MinuteBar("XYZ", signal.timestamp + timedelta(minutes=2), signal.entry_trigger,
                          signal.target_levels[2], signal.stop_price,
                          signal.target_levels[1], D("700"))
    service.observe_market_bar("XYZ", ambiguous, ambiguous.timestamp + timedelta(minutes=1))
    writer.flush()
    fills = [item.payload for item in store.records(record_type=CaptureRecordType.PAPER_FILL)]
    assert fills[0]["action"] == "ENTRY"
    contexts = store.records(record_type=CaptureRecordType.MANAGEMENT_CONTEXT)
    assert contexts and contexts[0].payload["environment"] == "PAPER"
    assert Decimal(contexts[0].payload["stop"]) == signal.stop_price
    assert any(item.get("label") == "FIRST_TARGET" for item in fills)
    # After 1R the stop is breakeven; conservative same-bar handling exits before targets.
    assert fills[-1]["label"] == "STOP"
    transitions = [item.payload for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)]
    exit_record = next(item for item in transitions if item["to"] == "PAPER_EXIT")
    assert {"realized_r", "mae_r", "mfe_r", "hold_seconds"} <= exit_record.keys()
    assert exit_record["mae_r"] == "0" and exit_record["mfe_r"] == "1"


def test_restart_recovery_duplicate_prevention_and_replay_equivalence(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "recover.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    _candidate, signal = service.observe(point(), account=account())
    assert signal is not None
    writer.flush()
    decision = store.records(record_type=CaptureRecordType.DECISION)[0]
    assert replay_captured_decision(store, decision.record_id).equivalent
    record = CaptureRecord.create(CaptureRecordType.DATA_QUALITY, "XYZ", T0, {"x": True})
    assert store.append_batch((record, record)) == (1, 1)
    writer.close()

    restarted_writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    restarted = WarriorForwardCaptureService(store, restarted_writer)
    stop_bar = MinuteBar("XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
                         signal.entry_trigger, signal.stop_price, signal.stop_price, D("100"))
    restarted.observe_market_bar("XYZ", stop_bar, stop_bar.timestamp + timedelta(minutes=1))
    restarted_writer.close()
    assert any(
        item.payload.get("to") == "PAPER_EXIT"
        for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)
    )
    assert store.integrity_check() == "ok"


def test_management_context_restores_stop_and_trailing_state(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "management.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    _candidate, signal = service.observe(point(), account=account())
    assert signal is not None
    writer.flush()
    before = service._paper["XYZ"]
    before.stop = signal.entry_trigger
    before.maximum_high = signal.entry_trigger + Decimal("2")
    service.observe_market_bar("XYZ", MinuteBar("XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger + D("0.015"), signal.entry_trigger + D("0.02"), signal.entry_trigger + D("0.01"), signal.entry_trigger + D("0.015"), D("100")), signal.timestamp + timedelta(minutes=2))
    writer.flush()
    writer.close()
    restarted_writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    restarted = WarriorForwardCaptureService(store, restarted_writer)
    state = restarted._paper["XYZ"]
    assert state.stop == signal.entry_trigger
    assert state.maximum_high == signal.entry_trigger + Decimal("2")
    restarted_writer.close()


def test_daily_report_uses_na_for_zero_trade_sample(capture) -> None:
    store, writer, service = capture
    service.observe(point(observation=scanner(bid=None, ask=None)))
    writer.flush()
    report = build_daily_report(store, date(2026, 8, 10))
    assert dict(report.funnel)["DISCOVERED"] == 1
    assert report.paper_trades == 0
    assert report.expectancy_r is None and report.profit_factor is None
    assert dict(report.missing_data_counts)["missing_bid_ask"] == 1
    assert persist_daily_report(store, report) == (1, 0)
    assert persist_daily_report(store, report) == (0, 1)


def test_capture_writer_is_bounded_fail_closed_and_gui_isolated(tmp_path: Path) -> None:
    entered = Event()
    release = Event()

    class SlowStore:
        def append_batch(self, records):
            entered.set()
            release.wait(2)
            return len(records), 0

    writer = ForwardCaptureWriter(SlowStore(), capacity=1, batch_size=1,
                                  flush_interval_seconds=0.01)
    records = tuple(
        CaptureRecord.create(CaptureRecordType.DATA_QUALITY, "XYZ", T0 + timedelta(seconds=i), {"i": i})
        for i in range(3)
    )
    writer.submit(records[0])
    assert entered.wait(1)
    writer.submit(records[1])
    writer.submit(records[2], timeout_seconds=0.01)
    assert writer.metrics().dropped_records == 0
    assert writer.metrics().synchronous_fallback_records == 1
    assert writer.metrics().gui_refresh_count == 0
    release.set()
    writer.close()
    source = Path("app/strategies/warrior_momentum/forward_queue.py").read_text(encoding="utf-8")
    assert "PySide6" not in source and "PyQt" not in source
