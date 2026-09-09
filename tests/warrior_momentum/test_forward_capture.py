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
from tests.test_support.session_clock import (
    create_session_paper_composition as create_paper_trading_command_composition,
    session_timestamp,
)
from app.strategies.warrior_momentum import (
    CaptureRecord, CaptureRecordType,
    FloatProvenance, ForwardCaptureWriter, ForwardCaptureStore,
    ForwardTransition, MinuteBar, PaperAccountContext,
    PointInTimeObservation, WarriorForwardCaptureService,
    build_daily_report, persist_daily_report, replay_captured_decision,
)
from app.strategies.warrior_momentum.desktop_sidecar import strategy_configuration_fingerprint
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
        quote_freshness_seconds=D("0.2"),
        last_price_observed_at=T0 + timedelta(minutes=20),
        last_price_freshness_seconds=D("0.2"), halt_state_known=True,
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


def test_stale_entry_critical_data_fails_closed_and_fresh_data_restores_eligibility(
    capture,
) -> None:
    _store, _writer, service = capture

    stale_candidate, stale_signal = service.observe(
        point(
            quote_freshness_seconds=D("5.1"),
            last_price_freshness_seconds=D("5.1"),
        ),
        account=account(),
    )

    assert stale_signal is None
    assert stale_candidate.status.value == "AWAITING_EXECUTION_DATA"
    assert "STALE_MARKET_DATA" in {
        code.value for code in stale_candidate.reason_codes
    }

    fresh_candidate, fresh_signal = service.observe(
        point(
            observation=scanner(timestamp=T0 + timedelta(minutes=21)),
            quote_observed_at=T0 + timedelta(minutes=21),
            last_price_observed_at=T0 + timedelta(minutes=21),
            quote_freshness_seconds=D("0.1"),
            last_price_freshness_seconds=D("0.1"),
        ),
        account=account(),
    )

    assert fresh_candidate.status.value == "ENTRY_READY"
    assert fresh_signal is not None


@pytest.mark.parametrize(
    ("quote_age", "last_age"),
    (
        (D("30.0"), D("0.1")),
        (D("0.1"), D("30.0")),
        (D("30.0"), D("30.0")),
    ),
    ids=("old-quote-fresh-last", "fresh-quote-old-last", "both-stale"),
)
def test_each_stale_market_component_independently_blocks_paper_submission(
    capture, quote_age: Decimal, last_age: Decimal,
) -> None:
    store, writer, service = capture

    candidate, signal = service.observe(
        point(
            session="AFTER_HOURS",
            quote_freshness_seconds=quote_age,
            last_price_freshness_seconds=last_age,
        ),
        account=account(),
    )
    writer.flush()

    assert candidate.setup is not None
    assert candidate.setup.state.value == "TRIGGERED"
    assert candidate.status.value == "AWAITING_EXECUTION_DATA"
    assert "STALE_MARKET_DATA" in {code.value for code in candidate.reason_codes}
    assert signal is None
    assert not store.records(record_type=CaptureRecordType.PAPER_FILL)


def test_stale_market_data_remains_visible_with_another_blocker(capture) -> None:
    store, writer, service = capture

    candidate, signal = service.observe(
        point(
            bars=(),
            historical_bars_available=False,
            quote_freshness_seconds=D("30"),
            last_price_freshness_seconds=D("0.1"),
        ),
        account=account(),
    )
    writer.flush()

    reasons = tuple(code.value for code in candidate.reason_codes)
    assert reasons[-2:] == ("NO_SETUP", "STALE_MARKET_DATA")
    assert candidate.status.value == "INELIGIBLE_FOR_EXECUTION"
    assert signal is None
    transitions = tuple(
        item.payload
        for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)
        if item.payload["to"] == "ENTRY_BLOCKED"
    )
    assert transitions
    assert tuple(transitions[-1]["reason_codes"][-2:]) == (
        "NO_SETUP", "STALE_MARKET_DATA",
    )
    assert "market_data" in {
        gate["gate"] for gate in transitions[-1]["blocking_gates"]
    }
    assert not store.records(record_type=CaptureRecordType.PAPER_FILL)


def test_after_hours_signal_reaches_normal_paper_gateway_once(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "after-hours-forward.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    after_hours = datetime(2026, 8, 10, 22, 0, tzinfo=UTC)
    composition = create_paper_trading_command_composition(at=after_hours)
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
            1, session_timestamp(1, at=after_hours), signal.symbol, "after-hours-test",
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


def test_authoritative_exit_submission_and_partial_fill_do_not_close(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "authoritative.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("0")}
    composition = create_paper_trading_command_composition(
        at=T0 + timedelta(minutes=20),
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
        position_average_cost_source=lambda _symbol: Decimal("10.20"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=bridge.submit_entry,
        paper_exit_submitter=bridge.ensure_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        shares = int(composition.order_book.open_orders()[0].quantity)
        composition.gateway.process_market_event(MarketEvent(
            1, session_timestamp(1, at=T0 + timedelta(minutes=20)), "XYZ", "test", MarketEventType.QUOTE,
            QuotePayload(signal.entry_trigger - D("0.01"), signal.entry_trigger,
                         D(shares), D(shares)),
        ))
        position["XYZ"] = Decimal(shares)

        stop_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.entry_trigger, signal.stop_price - D("0.01"),
            signal.stop_price - D("0.01"), D("100"),
        )
        service.observe_market_bar("XYZ", stop_bar, stop_bar.timestamp + timedelta(minutes=1))
        writer.flush()
        transitions = [item.payload["to"] for item in store.records(
            record_type=CaptureRecordType.STATE_TRANSITION
        )]
        # The stop was established from an ambiguous activation bar, but the
        # bar cannot retroactively prove that its low occurred after activation.
        assert "PAPER_EXIT_WORKING" not in transitions
        assert "PAPER_EXIT_REQUIRED" not in transitions
        assert "PAPER_EXIT" not in transitions
        assert "XYZ" in service.open_paper_symbols

        sell = next(order for order in composition.order_book.open_orders()
                    if order.request.side.value == "SELL")
        composition.gateway.process_market_event(MarketEvent(
            2, session_timestamp(2, at=T0 + timedelta(minutes=20)), "XYZ", "test", MarketEventType.QUOTE,
            QuotePayload(signal.stop_price - D("0.02"), signal.stop_price - D("0.01"),
                         D("1"), D("1")),
        ))
        position["XYZ"] = Decimal(shares - 1)
        next_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2), signal.stop_price,
            signal.stop_price, signal.stop_price - D("0.02"),
            signal.stop_price - D("0.01"), D("100"),
        )
        service.observe_market_bar("XYZ", next_bar, next_bar.timestamp + timedelta(minutes=1))
        writer.flush()
        assert "XYZ" in service.open_paper_symbols
        assert service._paper["XYZ"].remaining == shares - 1
        assert composition.order_book.get(sell.order_id).remaining_quantity == Decimal(shares - 1)
        assert service._paper["XYZ"].exit_reason == "STOP"
        assert not any(
            item.payload.get("to") == "PAPER_EXIT"
            for item in store.records(record_type=CaptureRecordType.STATE_TRANSITION)
        )

        composition.gateway.process_market_event(MarketEvent(
            3, session_timestamp(3, at=T0 + timedelta(minutes=20)), "XYZ", "test", MarketEventType.QUOTE,
            QuotePayload(signal.stop_price - D("0.03"), signal.stop_price - D("0.02"),
                         D(shares), D(shares)),
        ))
        position["XYZ"] = Decimal("0")
        final_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=3), signal.stop_price,
            signal.stop_price, signal.stop_price - D("0.03"),
            signal.stop_price - D("0.02"), D("100"),
        )
        service.observe_market_bar("XYZ", final_bar, final_bar.timestamp + timedelta(minutes=1))
        writer.flush()
        terminal = [item.payload for item in store.records(
            record_type=CaptureRecordType.STATE_TRANSITION
        ) if item.payload.get("to") == "PAPER_EXIT"]
        assert len(terminal) == 1
        assert terminal[0]["authority"] == "AUTHORITATIVE_POSITION_PROJECTION"
        assert "XYZ" not in service.open_paper_symbols
    finally:
        writer.close()
        composition.close()


def test_activation_bar_cannot_retroactively_stop_and_targets_remain_eligible(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "sune_activation.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("0")}
    composition = create_paper_trading_command_composition(
        at=T0 + timedelta(minutes=20),
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=bridge.submit_entry,
        paper_exit_submitter=bridge.ensure_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    def paper_quote(sequence: int, bid: str, ask: str) -> None:
        composition.gateway.process_market_event(MarketEvent(
            sequence, session_timestamp(sequence, at=T0 + timedelta(minutes=20)),
            "XYZ", "test", MarketEventType.QUOTE,
            QuotePayload(D(bid), D(ask), D("10000"), D("10000")),
        ))
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        shares = int(composition.order_book.open_orders()[0].quantity)
        paper_quote(1, str(signal.entry_trigger - D("0.01")), str(signal.entry_trigger))
        position["XYZ"] = Decimal(shares)

        ambiguous = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.entry_trigger, signal.stop_price - D("0.01"), signal.entry_trigger,
            D("100"),
        )
        service.observe_market_bar("XYZ", ambiguous, ambiguous.timestamp + timedelta(minutes=1))
        state = service._paper["XYZ"]
        assert state.exit_reason is None
        assert state.protective_stop_activated_at is not None

        first_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2), signal.entry_trigger,
            signal.target_levels[0] + D("0.01"), signal.entry_trigger,
            signal.target_levels[0], D("100"),
        )
        service.observe_market_bar("XYZ", first_bar, first_bar.timestamp + timedelta(minutes=1))
        state = service._paper["XYZ"]
        assert state.exit_reason == "FIRST_TARGET"
        assert state.first_taken is False
        first_quantity = state.first_quantity

        paper_quote(2, str(signal.target_levels[0]), str(signal.target_levels[0] + D("0.01")))
        position["XYZ"] = Decimal(shares - first_quantity)
        second_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=3), signal.target_levels[0],
            signal.target_levels[1] + D("0.01"), signal.target_levels[0],
            signal.target_levels[1], D("100"),
        )
        service.observe_market_bar("XYZ", second_bar, second_bar.timestamp + timedelta(minutes=1))
        state = service._paper["XYZ"]
        assert state.first_taken is True
        assert state.exit_reason == "SECOND_TARGET"
        second_quantity = state.second_quantity

        paper_quote(3, str(signal.target_levels[1]), str(signal.target_levels[1] + D("0.01")))
        position["XYZ"] = Decimal(shares - first_quantity - second_quantity)
        final_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=4), signal.target_levels[1],
            signal.target_levels[1], signal.target_levels[1], signal.target_levels[1], D("100"),
        )
        service.observe_market_bar("XYZ", final_bar, final_bar.timestamp + timedelta(minutes=1))
        state = service._paper["XYZ"]
        assert state.second_taken is True
        assert state.remaining == shares - first_quantity - second_quantity
    finally:
        writer.close()
        composition.close()


def test_unavailable_exit_keeps_authoritative_position_open_and_critical(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "unavailable.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=lambda *_args: False,
        paper_position_quantity_source=lambda _symbol: Decimal("100"),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        stop_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.entry_trigger, signal.stop_price - D("0.01"),
            signal.stop_price - D("0.01"), D("100"),
        )
        service.observe_market_bar("XYZ", stop_bar, stop_bar.timestamp + timedelta(minutes=1))
        writer.flush()
        transitions = [item.payload for item in store.records(
            record_type=CaptureRecordType.STATE_TRANSITION
        )]
        assert any(item["to"] == "PAPER_EXIT_REQUIRED" for item in transitions)
        assert any(
            item["to"] == "PAPER_POSITION_CONTRADICTION"
            and item["severity"] == "CRITICAL"
            for item in transitions
        )
        assert not any(item["to"] == "PAPER_EXIT" for item in transitions)
        assert service._paper["XYZ"].remaining == 100
    finally:
        writer.close()


def test_zero_fill_terminal_entry_retires_analytical_ownership(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "cancelled-entry.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_position_quantity_source=lambda _symbol: Decimal("0"),
        paper_execution_ownership_source=lambda _symbol: False,
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        next_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.entry_trigger, signal.stop_price + D("0.01"),
            signal.entry_trigger, D("100"),
        )
        service.observe_market_bar("XYZ", next_bar, next_bar.timestamp + timedelta(minutes=1))
        writer.flush()
        assert "XYZ" not in service.open_paper_symbols
        transitions = [item.payload for item in store.records(
            record_type=CaptureRecordType.STATE_TRANSITION
        )]
        assert any(
            item["to"] == "ENTRY_BLOCKED"
            and "ENTRY_TERMINATED_WITHOUT_POSITION" in item["reason_codes"]
            for item in transitions
        )
        contexts = store.records(record_type=CaptureRecordType.MANAGEMENT_CONTEXT)
        assert contexts[-1].payload["phase"] == "ENTRY_CANCELLED"
    finally:
        writer.close()


def test_analytical_closed_authoritative_open_surfaces_critical_contradiction(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "contradiction.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_position_quantity_source=lambda _symbol: Decimal("100"),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        service._last_transition["XYZ"] = ForwardTransition.PAPER_EXIT
        safe_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.entry_trigger, signal.stop_price + D("0.01"),
            signal.entry_trigger, D("100"),
        )
        service.observe_market_bar("XYZ", safe_bar, safe_bar.timestamp + timedelta(minutes=1))
        writer.flush()
        contradictions = [item.payload for item in store.records(
            record_type=CaptureRecordType.STATE_TRANSITION
        ) if item.payload.get("to") == "PAPER_POSITION_CONTRADICTION"]
        assert contradictions
        assert contradictions[-1]["severity"] == "CRITICAL"
        assert contradictions[-1]["authoritative_remaining"] == 100
        assert contradictions[-1]["new_same_symbol_execution"] == "FAIL_CLOSED"
        assert "XYZ" in service.open_paper_symbols
    finally:
        writer.close()


def test_restart_recovery_duplicate_prevention_and_replay_equivalence(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "recover.sqlite3")
    fingerprint = strategy_configuration_fingerprint()
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01,
                                  configuration_fingerprint=fingerprint)
    service = WarriorForwardCaptureService(store, writer,
                                           configuration_fingerprint=fingerprint)
    _candidate, signal = service.observe(point(), account=account())
    assert signal is not None
    writer.flush()
    decision = store.records(record_type=CaptureRecordType.DECISION)[0]
    assert replay_captured_decision(store, decision.record_id).equivalent
    record = CaptureRecord.create(CaptureRecordType.DATA_QUALITY, "XYZ", T0, {"x": True})
    assert store.append_batch((record, record)) == (1, 1)
    writer.close()

    restarted_writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01,
                                            configuration_fingerprint=fingerprint)
    restarted = WarriorForwardCaptureService(store, restarted_writer,
                                             configuration_fingerprint=fingerprint)
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
    fingerprint = strategy_configuration_fingerprint()
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01,
                                  configuration_fingerprint=fingerprint)
    service = WarriorForwardCaptureService(store, writer,
                                           configuration_fingerprint=fingerprint)
    _candidate, signal = service.observe(point(), account=account())
    assert signal is not None
    writer.flush()
    before = service._paper["XYZ"]
    before.stop = signal.entry_trigger
    before.maximum_high = signal.entry_trigger + Decimal("2")
    before.protective_stop_activated_at = signal.timestamp + timedelta(minutes=1)
    service.observe_market_bar("XYZ", MinuteBar("XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger + D("0.015"), signal.entry_trigger + D("0.02"), signal.entry_trigger + D("0.01"), signal.entry_trigger + D("0.015"), D("100")), signal.timestamp + timedelta(minutes=2))
    writer.flush()
    writer.close()
    restarted_writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01,
                                            configuration_fingerprint=fingerprint)
    restarted = WarriorForwardCaptureService(store, restarted_writer,
                                             configuration_fingerprint=fingerprint)
    state = restarted._paper["XYZ"]
    assert state.stop == signal.entry_trigger
    assert state.maximum_high == signal.entry_trigger + Decimal("2")
    assert state.protective_stop_activated_at == signal.timestamp + timedelta(minutes=1)
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


def test_daily_report_excludes_authoritative_projection_exit_from_performance(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "authoritative-report.sqlite3")
    store.append_batch((CaptureRecord.create(
        CaptureRecordType.STATE_TRANSITION,
        "AUTH",
        T0,
        {
            "from": ForwardTransition.PAPER_EXIT_WORKING,
            "to": ForwardTransition.PAPER_EXIT,
            "reason_codes": [],
            "authoritative_remaining": 0,
            "authority": "AUTHORITATIVE_POSITION_PROJECTION",
        },
        identity_parts=("AUTHORITATIVE",),
    ),))

    report = build_daily_report(store, T0.date())

    assert report.wins is None
    assert report.losses is None
    assert report.scratches is None
    assert report.total_r is None
    assert report.expectancy_r is None
    assert report.profit_factor is None
    assert report.maximum_intraday_drawdown_r is None
    assert report.average_mae_r is None
    assert report.average_mfe_r is None


def test_daily_report_mixed_exits_include_only_analytical_performance(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "mixed-report.sqlite3")
    store.append_batch((
        CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION,
            "AUTH",
            T0,
            {
                "from": ForwardTransition.PAPER_EXIT_WORKING,
                "to": ForwardTransition.PAPER_EXIT,
                "reason_codes": [],
                "authoritative_remaining": 0,
                "authority": "AUTHORITATIVE_POSITION_PROJECTION",
            },
            identity_parts=("AUTHORITATIVE",),
        ),
        CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION,
            "ANALYTICAL",
            T0 + timedelta(seconds=1),
            {
                "from": ForwardTransition.PAPER_ENTRY,
                "to": ForwardTransition.PAPER_EXIT,
                "reason_codes": [],
                "realized_r": "1.25",
                "mae_r": "-0.40",
                "mfe_r": "1.80",
                "hold_seconds": "60",
            },
            identity_parts=("ANALYTICAL",),
        ),
    ))

    report = build_daily_report(store, T0.date())

    assert report.wins == 1
    assert report.losses == 0
    assert report.scratches == 0
    assert report.total_r == D("1.25")
    assert report.expectancy_r == D("1.25")
    assert report.profit_factor is None
    assert report.maximum_intraday_drawdown_r == D("0")
    assert report.average_mae_r == D("-0.40")
    assert report.average_mfe_r == D("1.80")


def test_daily_report_preserves_historical_balances_and_fingerprint_boundaries(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "bounded-report.sqlite3")
    target = date(2026, 8, 11)
    start = datetime(2026, 8, 11, 4, tzinfo=UTC)

    def session(action: str, at: datetime, fingerprint: str, identity: str):
        return CaptureRecord.create(
            CaptureRecordType.OBSERVATION_SESSION,
            "WARRIOR_MOMENTUM_V1",
            at,
            {"action": action, "configuration_fingerprint": fingerprint},
            identity_parts=(identity,),
        )

    store.append_batch((
        session("START", start - timedelta(days=2), "old", "old-start"),
        CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, "OLD", start - timedelta(days=2),
            {"action": "ENTRY"}, identity_parts=("old-entry",),
        ),
        session("END", start - timedelta(days=1, hours=2), "old", "old-end"),
        session("START", start - timedelta(hours=2), "target", "target-start"),
        CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, "OPEN", start - timedelta(hours=1),
            {"action": "ENTRY"}, identity_parts=("target-entry",),
        ),
        CaptureRecord.create(
            CaptureRecordType.COUNTERFACTUAL, "TRACKED", start - timedelta(minutes=30),
            {"action": "START"}, identity_parts=("counter-start",),
        ),
        CaptureRecord.create(
            CaptureRecordType.DISCOVERY, "TODAY", start + timedelta(hours=2),
            {"stocks_in_play": ["HIGH_RELATIVE_VOLUME"]},
            identity_parts=("today",),
        ),
        CaptureRecord.create(
            CaptureRecordType.DATA_QUALITY, "TODAY", start + timedelta(hours=2),
            {"missing_bid_ask": True}, identity_parts=("quality",),
        ),
        CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, "OPEN", start + timedelta(days=1),
            {"action": "EXIT"}, identity_parts=("future-exit",),
        ),
        CaptureRecord.create(
            CaptureRecordType.COUNTERFACTUAL, "TRACKED", start + timedelta(days=1),
            {"action": "END"}, identity_parts=("future-counter-end",),
        ),
        CaptureRecord.create(
            CaptureRecordType.DISCOVERY, "FUTURE", start + timedelta(days=1),
            {"stocks_in_play": ["HIGH_RELATIVE_VOLUME"]},
            identity_parts=("future-discovery",),
        ),
        CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION, "FUTURE", start + timedelta(days=1),
            {"to": ForwardTransition.NEAR}, identity_parts=("future-transition",),
        ),
        CaptureRecord.create(
            CaptureRecordType.DATA_QUALITY, "FUTURE", start + timedelta(days=1),
            {"missing_volume": True}, identity_parts=("future-quality",),
        ),
        session("END", start + timedelta(days=1, hours=1), "target", "target-end"),
    ))

    report = build_daily_report(
        store, target, configuration_fingerprint="target",
    )

    assert dict(report.funnel)["DISCOVERED"] == 1
    assert dict(report.funnel)["STOCKS_IN_PLAY"] == 1
    assert dict(report.funnel)["NEAR"] == 0
    assert dict(report.missing_data_counts) == {"missing_bid_ask": 1}
    assert report.open_paper_positions == 1
    assert report.counterfactual_starts == 0
    assert report.tracked_counterfactuals == 1


def test_daily_report_uses_sequence_order_for_intraday_drawdown(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "sequence-report.sqlite3")
    values = ("2", "-1", "-1", "-1", "2")
    chronological_positions = (0, 1, 4, 2, 3)
    records = tuple(
        CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION,
            f"S{index}",
            T0 + timedelta(minutes=chronological_positions[index]),
            {
                "to": ForwardTransition.PAPER_EXIT,
                "realized_r": value,
                "mae_r": "-0.5",
                "mfe_r": "2",
            },
            identity_parts=(str(index),),
        )
        for index, value in enumerate(values)
    )
    store.append_batch(records)

    report = build_daily_report(store, T0.date())

    assert report.total_r == D("1")
    assert report.maximum_intraday_drawdown_r == D("3")


@pytest.mark.parametrize(
    ("target", "expected_start", "expected_end", "hours"),
    (
        (
            date(2026, 3, 8),
            datetime(2026, 3, 8, 5, tzinfo=UTC),
            datetime(2026, 3, 9, 4, tzinfo=UTC),
            23,
        ),
        (
            date(2026, 11, 1),
            datetime(2026, 11, 1, 4, tzinfo=UTC),
            datetime(2026, 11, 2, 5, tzinfo=UTC),
            25,
        ),
    ),
)
def test_daily_report_builds_independent_eastern_midnight_bounds(
    target: date,
    expected_start: datetime,
    expected_end: datetime,
    hours: int,
) -> None:
    class RecordingStore:
        bounds = None

        def records_for_daily_report(self, *, start_utc, end_utc):
            self.bounds = (start_utc, end_utc)
            return ()

    store = RecordingStore()

    build_daily_report(store, target)

    assert store.bounds == (expected_start, expected_end)
    assert (expected_end - expected_start).total_seconds() == hours * 3600


def test_daily_report_python_eastern_boundary_filter_remains_authoritative(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "eastern-boundary.sqlite3")
    start = datetime.fromisoformat("2026-03-08T00:00:00-05:00")
    end = datetime.fromisoformat("2026-03-09T00:00:00-04:00")
    timestamps = (
        ("BEFORE", start - timedelta(microseconds=1)),
        ("START", start),
        ("INSIDE", datetime(2026, 3, 8, 12, tzinfo=UTC)),
        ("BEFORE_END", end - timedelta(microseconds=1)),
        ("END", end),
        ("AFTER", end + timedelta(microseconds=1)),
    )
    store.append_batch(tuple(
        CaptureRecord.create(
            CaptureRecordType.DISCOVERY, symbol, timestamp,
            {"stocks_in_play": []}, identity_parts=(symbol,),
        )
        for symbol, timestamp in timestamps
    ))

    report = build_daily_report(store, date(2026, 3, 8))

    assert dict(report.funnel)["DISCOVERED"] == 3


def test_daily_report_malformed_analytical_exit_still_fails(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "malformed-report.sqlite3")
    store.append_batch((CaptureRecord.create(
        CaptureRecordType.STATE_TRANSITION,
        "MALFORMED",
        T0,
        {"to": ForwardTransition.PAPER_EXIT, "mae_r": "-0.4", "mfe_r": "1.8"},
        identity_parts=("missing-realized",),
    ),))

    with pytest.raises(KeyError, match="realized_r"):
        build_daily_report(store, T0.date())


def test_persist_daily_report_uses_same_day_timestamp_without_materializing_payloads(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "bounded-persistence.sqlite3")
    target_timestamp = T0 + timedelta(hours=1)
    store.append_batch((
        CaptureRecord.create(
            CaptureRecordType.MINUTE_BAR, "TARGET", target_timestamp,
            {"large": "x" * 10_000}, identity_parts=("target",),
        ),
        CaptureRecord.create(
            CaptureRecordType.DAILY_REPORT, "WARRIOR_MOMENTUM_V1",
            target_timestamp + timedelta(hours=1), {"existing": True},
            identity_parts=("existing-report",),
        ),
        CaptureRecord.create(
            CaptureRecordType.MINUTE_BAR, "FUTURE", T0 + timedelta(days=1),
            {"large": "y" * 10_000}, identity_parts=("future",),
        ),
    ))
    report = build_daily_report(store, T0.date())
    materialized_before = store.records_materialized_total()

    assert persist_daily_report(store, report) == (1, 0)
    assert store.records_materialized_total() == materialized_before
    persisted = tuple(
        record for record in store.records(record_type=CaptureRecordType.DAILY_REPORT)
        if record.payload.get("trading_date") == T0.date().isoformat()
    )
    assert len(persisted) == 1
    assert persisted[0].timestamp == target_timestamp


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
