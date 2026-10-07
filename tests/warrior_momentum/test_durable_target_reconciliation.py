from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.order_cancellation import OrderCancellationRequest
from app.paper_trading.order_models import OrderStatus, OrderTerminalReason
from app.performance_diagnostics import performance_diagnostics
from app.strategies.warrior_momentum import (
    ForwardCaptureStore,
    ForwardCaptureWriter,
    WarriorForwardCaptureService,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousManagementReadiness,
    AutonomousPaperExecutionBridge,
    AutonomousPaperReadiness,
    PaperExitSubmissionDecision,
    PaperExitSubmissionFailureReason,
    PaperExitSubmissionState,
)
from app.strategies.warrior_momentum.forward_runtime import (
    _PaperState,
    _management_context_record,
    management_context_available,
)
from tests.test_support.session_clock import (
    FixedClock,
    create_session_paper_composition,
    session_timestamp,
)
from tests.warrior_momentum.test_forward_capture import account, point


D = Decimal
NOW = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
LIFECYCLE = (
    "WARRIOR_MOMENTUM_V1|XRPN|LEGACY_EPISODE|"
    "f2f16c02cc98fb114301c6afc97a90075d4d56444ac415d3dcd57f0be0663589"
)
STALE_LIFECYCLE = (
    "WARRIOR_MOMENTUM_V1|XRPN|LEGACY_EPISODE|"
    "77b822c79c844f20d93bd7365fcbd08da2539342b1363d1fc1cd17f485574868"
)


class _CountingSubmitter:
    def __init__(self, bridge: AutonomousPaperExecutionBridge) -> None:
        self.bridge = bridge
        self.order_book = bridge.order_book
        self.calls: list[tuple[str, int, Decimal, str, str | None]] = []
        self.results: list[PaperExitSubmissionDecision] = []

    def submit(
        self, symbol, quantity, price, reason, lifecycle,
        *, target_stage_only=False,
    ):
        self.calls.append((symbol, quantity, price, reason, lifecycle))
        result = self.bridge.ensure_exit(
            symbol, quantity, price, reason, lifecycle,
            target_stage_only=target_stage_only,
        )
        self.results.append(result)
        return result


class _FalseSuccessSubmitter:
    def __init__(self, order_book) -> None:
        self.order_book = order_book
        self.calls: list[str] = []

    def submit(
        self, symbol, quantity, price, reason, lifecycle,
        *, target_stage_only=False,
    ):
        del quantity, price, target_stage_only
        self.calls.append(reason)
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.WORKING,
            symbol,
            lifecycle,
            reason,
            order_id="SUCCESS-LIKE-WITHOUT-DURABLE-ORDER",
            activation_timestamp=NOW,
        )


class _UnavailableSubmitter:
    def __init__(self, order_book) -> None:
        self.order_book = order_book

    def submit(
        self, symbol, quantity, price, reason, lifecycle,
        *, target_stage_only=False,
    ):
        del quantity, price, target_stage_only
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.UNAVAILABLE,
            symbol,
            lifecycle,
            reason,
            failure_reason=PaperExitSubmissionFailureReason.MANAGEMENT_NOT_READY,
        )


def _harness(tmp_path: Path):
    position = {"XRPN": D("0")}
    composition = create_session_paper_composition(at=NOW)
    store = ForwardCaptureStore(tmp_path / "durable-target.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)

    seed = WarriorForwardCaptureService(store, writer)
    _candidate, base_signal = seed.observe(point(), account=account())
    assert base_signal is not None
    signal = replace(
        base_signal,
        symbol="XRPN",
        entry_trigger=D("17.56"),
        reference_price=D("17.56"),
        stop_price=D("16.57"),
        risk_per_share=D("0.99"),
        target_levels=(D("18.427490"), D("19.294980"), D("20.162470")),
        taxonomy_execution_identity=LIFECYCLE,
        structural_stop_price=D("16.57"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
        management_context_source=lambda _symbol: LIFECYCLE,
    )
    assert bridge.submit_entry(signal, 25, D("24.75"))
    _quote(composition, 1, "17.55", "17.56", "25")
    position["XRPN"] = D("25")
    assert bridge._authoritative_quantity("XRPN") == D("25")
    submitter = _CountingSubmitter(bridge)
    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_exit_submitter=submitter.submit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    service._paper["XRPN"] = _PaperState(
        signal=signal,
        entry_price=D("17.56"),
        initial_quantity=25,
        remaining=25,
        stop=D("16.57"),
        first_quantity=23,
        second_quantity=1,
        managed_quantity=25,
        authoritative_position_seen=True,
    )
    stop = bridge.ensure_exit("XRPN", 25, D("16.57"), "STOP", LIFECYCLE)
    assert stop.protection_active
    return composition, bridge, submitter, writer, service, position


def _active_sells(composition):
    return tuple(
        order
        for order in composition.order_book.open_orders_for_symbol("XRPN")
        if order.request.side.value == "SELL"
    )


def _events_since(index: int):
    events = performance_diagnostics.reconciliation_metrics()["management_events"]
    return tuple(events[index:])


def _quote(composition, sequence: int, bid: str, ask: str, size: str):
    return composition.gateway.process_market_event(MarketEvent(
        sequence,
        session_timestamp(sequence),
        "XRPN",
        "durable-target-regression",
        MarketEventType.QUOTE,
        QuotePayload(D(bid), D(ask), D(size), D(size)),
    ))


def test_xrpn_exact_missing_target_is_created_verified_and_idempotent(tmp_path):
    composition, _bridge, submitter, writer, service, _position = _harness(tmp_path)
    before_events = len(
        performance_diagnostics.reconciliation_metrics()["management_events"]
    )
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW) is True
        sells = _active_sells(composition)
        targets = tuple(o for o in sells if o.request.order_type.value == "LIMIT")
        stops = tuple(o for o in sells if o.request.order_type.value == "STOP")
        assert len(targets) == 1
        assert targets[0].request.execution_reason == "FIRST_TARGET"
        assert targets[0].remaining_quantity == D("23")
        assert targets[0].request.limit_price == D("18.427490")
        assert len(stops) == 1
        assert stops[0].remaining_quantity == D("25")
        assert stops[0].request.stop_price == D("16.57")

        events = _events_since(before_events)
        restored = next(e for e in events if e["state"] == "PROFIT_TARGET_RESTORED")
        complete = next(
            e for e in events if e["state"] == "BRACKET_RECONCILIATION_COMPLETE"
        )
        assert restored["desired_target_qty"] == 23
        assert restored["actual_target_working_qty"] == 23
        assert complete["target_working_qty"] == 23
        assert complete["stop_working_qty"] == 25

        first_target_calls = sum(c[3] == "FIRST_TARGET" for c in submitter.calls)
        first_ids = tuple(o.order_id for o in targets)
        assert service.reconcile_authoritative_protection("XRPN", NOW) is True
        assert sum(c[3] == "FIRST_TARGET" for c in submitter.calls) == first_target_calls
        targets = tuple(
            o for o in _active_sells(composition)
            if o.request.order_type.value == "LIMIT"
        )
        assert tuple(o.order_id for o in targets) == first_ids
    finally:
        writer.close()
        composition.close()


def test_success_like_submission_without_durable_target_is_incomplete(tmp_path):
    composition, _bridge, _submitter, writer, service, _position = _harness(tmp_path)
    false_submitter = _FalseSuccessSubmitter(composition.order_book)
    service._paper_exit_submitter = false_submitter.submit
    before_events = len(
        performance_diagnostics.reconciliation_metrics()["management_events"]
    )
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW) is False
        assert false_submitter.calls == ["FIRST_TARGET"]
        assert not tuple(
            o for o in _active_sells(composition)
            if o.request.order_type.value == "LIMIT"
        )
        events = _events_since(before_events)
        incomplete = next(
            e for e in events if e["state"] == "BRACKET_RECONCILIATION_INCOMPLETE"
        )
        assert incomplete["reason"] == "TARGET_NOT_DURABLE"
        assert incomplete["desired_target_qty"] == 23
        assert incomplete["actual_target_working_qty"] == 0
        assert not any(
            e["state"] == "BRACKET_RECONCILIATION_COMPLETE" for e in events
        )
        assert not any(e["state"] == "PROFIT_TARGET_RESTORED" for e in events)
    finally:
        writer.close()
        composition.close()


def test_target_submission_failure_preserves_bridge_failure_subtype(tmp_path):
    composition, _bridge, _submitter, writer, service, _position = _harness(tmp_path)
    service._paper_exit_submitter = _UnavailableSubmitter(
        composition.order_book
    ).submit
    before_events = len(
        performance_diagnostics.reconciliation_metrics()["management_events"]
    )
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW) is False
        incomplete = next(
            event for event in reversed(_events_since(before_events))
            if event["state"] == "BRACKET_RECONCILIATION_INCOMPLETE"
        )
        assert incomplete["reason"] == "TARGET_SUBMISSION_FAILED"
        assert incomplete["target_submission_role"] == "FIRST_TARGET"
        assert incomplete["target_submission_state"] == "UNAVAILABLE"
        assert (
            incomplete["target_submission_failure_reason"]
            == "MANAGEMENT_NOT_READY"
        )
        state = service._paper["XRPN"]
        assert state.exit_reason is None
        assert state.exit_price is None
        assert state.active_exit_role != "FIRST_TARGET"
        assert len(tuple(
            order for order in _active_sells(composition)
            if order.request.order_type.value == "STOP"
        )) == 1
    finally:
        writer.close()
        composition.close()


def test_xrpn_partial_stale_entry_restart_restores_correlated_target(tmp_path):
    paper_path = tmp_path / "xrpn-restart-paper.sqlite3"
    capture_path = tmp_path / "xrpn-restart-capture.sqlite3"
    clock = FixedClock(NOW - timedelta(minutes=5))
    position = {"XRPN": D("0")}
    first = create_session_paper_composition(
        at=NOW,
        clock=clock,
        persistence_path=str(paper_path),
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    capture_store = ForwardCaptureStore(capture_path)
    capture_writer = ForwardCaptureWriter(capture_store, flush_interval_seconds=0.01)
    seed = WarriorForwardCaptureService(capture_store, capture_writer)
    _candidate, base_signal = seed.observe(point(), account=account())
    assert base_signal is not None
    signal = replace(
        base_signal,
        symbol="XRPN",
        entry_trigger=D("17.56"),
        reference_price=D("17.56"),
        stop_price=D("16.57"),
        risk_per_share=D("0.99"),
        target_levels=(D("18.427490"), D("19.294980"), D("20.162470")),
        taxonomy_execution_identity=LIFECYCLE,
        structural_stop_price=D("16.57"),
    )
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service,
        first.order_command_factory,
        order_book=first.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    try:
        stale_signal = replace(
            signal,
            entry_trigger=D("15.47"),
            reference_price=D("15.47"),
            stop_price=D("15.10"),
            taxonomy_execution_identity=STALE_LIFECYCLE,
            taxonomy_opportunity_id="XRPN-STALE-OPPORTUNITY",
            structural_episode_id="XRPN-STALE-EPISODE",
            structural_stop_price=D("15.10"),
        )
        assert bridge.submit_entry(stale_signal, 1, D("0.37"))
        first.gateway.process_market_event(MarketEvent(
            -2, clock.value + timedelta(seconds=1), "XRPN",
            "stale-lifecycle-entry", MarketEventType.QUOTE,
            QuotePayload(D("15.46"), D("15.47"), D("1"), D("1")),
        ))
        position["XRPN"] = D("1")
        assert bridge.ensure_exit(
            "XRPN", 1, D("15.46"), "SESSION_CLOSE", STALE_LIFECYCLE,
        ).state is PaperExitSubmissionState.SUBMITTED
        first.gateway.process_market_event(MarketEvent(
            -1, clock.value + timedelta(seconds=2), "XRPN",
            "stale-lifecycle-exit", MarketEventType.QUOTE,
            QuotePayload(D("15.46"), D("15.47"), D("1"), D("1")),
        ))
        position["XRPN"] = D("0")
        clock.value = NOW

        assert bridge.submit_entry(signal, 47, D("46.53"))
        entry = first.order_book.open_orders_for_symbol("XRPN")[0]
        clock.value = NOW + timedelta(seconds=10)
        reports = first.gateway.process_market_event(MarketEvent(
            1, clock.value, "XRPN", "restart-regression", MarketEventType.QUOTE,
            QuotePayload(D("17.55"), D("17.56"), D("25"), D("25")),
        ))
        assert reports[0].order.filled_quantity == D("25")
        position["XRPN"] = D("25")

        clock.value = NOW + timedelta(minutes=1)
        assert first.gateway.process_market_event(MarketEvent(
            2, clock.value, "XRPN", "restart-regression", MarketEventType.QUOTE,
            QuotePayload(D("18.70"), D("18.71"), D("100"), D("100")),
        )) == ()
        terminal_entry = first.order_book.get(entry.order_id)
        assert terminal_entry.status is OrderStatus.EXPIRED
        assert terminal_entry.terminal_reason is OrderTerminalReason.ENTRY_STALE
        assert terminal_entry.filled_quantity == D("25")

        stop = bridge.ensure_exit("XRPN", 25, D("16.57"), "STOP", LIFECYCLE)
        assert first.gateway.amend_protective_stop(
            stop.order_id, 25, D("16.57"),
        )
        durable_stop = first.order_book.get(stop.order_id)
        assert durable_stop.status is OrderStatus.ACCEPTED
        assert durable_stop.request.metadata["reservation_mode"] == "ACTIVE_PROTECTION"
        assert durable_stop.request.metadata.get("correlated_target_order_id") is None

        state = _PaperState(
            signal=signal,
            entry_price=D("17.56"),
            initial_quantity=25,
            remaining=25,
            stop=D("16.57"),
            first_quantity=23,
            second_quantity=1,
            managed_quantity=25,
            authoritative_position_seen=True,
        )
        stale_state = replace(state, signal=stale_signal)
        capture_store.append_batch((
            _management_context_record(
                "XRPN", clock.value, stale_signal, stale_state,
            ),
        ))
    finally:
        capture_writer.close()
        first.close()

    restarted = create_session_paper_composition(
        at=clock.value,
        clock=clock,
        persistence_path=str(paper_path),
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
    )
    recovered = AutonomousPaperExecutionBridge(
        restarted.trading_service,
        restarted.order_command_factory,
        order_book=restarted.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, D("0")),
        management_context_source=lambda symbol: management_context_available(
            capture_path, symbol,
        ),
    )
    try:
        assert (
            management_context_available(capture_path, "XRPN")
            == STALE_LIFECYCLE
        )
        before_events = len(
            performance_diagnostics.reconciliation_metrics()[
                "protection_events"
            ]
        )
        assert recovered.reconcile() is AutonomousPaperReadiness.READY
        assert (
            recovered.management_readiness("XRPN")
            is AutonomousManagementReadiness.RECOVERED_READY
        )
        events = performance_diagnostics.reconciliation_metrics()[
            "protection_events"
        ][before_events:]
        assert any(
            event["state"] == "STALE_MANAGEMENT_CONTEXT_IGNORED"
            and event["symbol"] == "XRPN"
            for event in events
        )
        assert any(
            event["state"] == "RECOVERED_LIFECYCLE_SELECTED"
            and event["symbol"] == "XRPN"
            for event in events
        )
        target = recovered.ensure_exit(
            "XRPN", 23, D("18.427490"), "FIRST_TARGET", LIFECYCLE,
            target_stage_only=True,
        )
        assert target.state is PaperExitSubmissionState.SUBMITTED
        assert target.role == "FIRST_TARGET"
        assert target.failure_reason is None
        sells = restarted.order_book.open_orders_for_symbol("XRPN")
        targets = tuple(
            order for order in sells
            if order.request.execution_reason == "FIRST_TARGET"
        )
        stops = tuple(
            order for order in sells if order.request.order_type.value == "STOP"
        )
        assert len(targets) == 1 and targets[0].remaining_quantity == D("23")
        assert targets[0].request.limit_price == D("18.427490")
        assert len(stops) == 1 and stops[0].remaining_quantity == D("25")
        assert stops[0].request.metadata["reservation_mode"] == "CONTINGENT_OCO"
        assert (
            stops[0].request.metadata["correlated_target_order_id"]
            == targets[0].order_id
        )
    finally:
        restarted.close()


def test_cancelled_target_is_seen_as_zero_then_restored(tmp_path):
    composition, bridge, submitter, writer, service, _position = _harness(tmp_path)
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW)
        target = next(
            o for o in _active_sells(composition)
            if o.request.order_type.value == "LIMIT"
        )
        cancellation = composition.gateway.cancel_order(OrderCancellationRequest(
            request_id="cancel-xrpn-first-target",
            session_id=composition.session_id,
            account_id=composition.account_id,
            broker_order_id=target.order_id,
            client_order_id=target.request.client_order_id,
        ))
        assert cancellation is not None and cancellation.accepted
        bridge._reconcile_terminal_exits()
        assert service._durable_exit_leg(
            service._paper["XRPN"], "FIRST_TARGET",
        ).working_quantity == 0
        result = service.reconcile_authoritative_protection(
            "XRPN", NOW + timedelta(seconds=2),
        )
        debug_events = performance_diagnostics.reconciliation_metrics()[
            "management_events"
        ][-8:]
        incomplete = next(
            (e for e in reversed(debug_events)
             if e["state"] == "BRACKET_RECONCILIATION_INCOMPLETE"),
            {},
        )
        assert result, (
            incomplete.get("reason"),
            tuple((item.state.value, item.reason, item.order_id)
                  for item in submitter.results[-3:]),
            bridge._active_by_symbol.get("XRPN"),
            bridge._authoritative_quantity("XRPN"),
            bridge.management_readiness("XRPN").value,
            tuple(
                (
                    o.request.order_type.value,
                    o.request.strategy_lifecycle_id,
                    int(o.remaining_quantity),
                )
                for o in _active_sells(composition)
            ),
        )
        targets = tuple(
            o for o in _active_sells(composition)
            if o.request.execution_reason == "FIRST_TARGET"
        )
        assert len(targets) == 1
        assert targets[0].order_id != target.order_id
        assert targets[0].remaining_quantity == D("23")
    finally:
        writer.close()
        composition.close()


def test_target_lifecycle_mismatch_fails_closed(tmp_path):
    composition, bridge, submitter, writer, service, _position = _harness(tmp_path)
    wrong = "WARRIOR_MOMENTUM_V1|XRPN|LEGACY_EPISODE|wrong"
    misplaced = bridge.ensure_exit(
        "XRPN", 23, D("18.427490"), "FIRST_TARGET", LIFECYCLE,
        target_stage_only=True,
    )
    assert misplaced.protection_active
    misplaced_order = composition.order_book.get(misplaced.order_id)
    composition.order_book.update(replace(
        misplaced_order,
        request=replace(
            misplaced_order.request,
            strategy_lifecycle_id=wrong,
        ),
        updated_at=NOW + timedelta(seconds=1),
    ))
    before = len(submitter.calls)
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW) is False
        assert len(submitter.calls) == before
    finally:
        writer.close()
        composition.close()


def test_target_quantity_mismatch_is_replaced_then_verified(tmp_path):
    composition, bridge, _submitter, writer, service, _position = _harness(tmp_path)
    stale = bridge.ensure_exit(
        "XRPN", 5, D("18.427490"), "FIRST_TARGET", LIFECYCLE,
        target_stage_only=True,
    )
    try:
        assert stale.protection_active
        assert service.reconcile_authoritative_protection("XRPN", NOW)
        targets = tuple(
            o for o in _active_sells(composition)
            if o.request.execution_reason == "FIRST_TARGET"
        )
        assert len(targets) == 1
        assert targets[0].remaining_quantity == D("23")
        assert targets[0].order_id != stale.order_id
    finally:
        writer.close()
        composition.close()


def test_target_price_unavailable_is_incomplete(tmp_path):
    composition, _bridge, submitter, writer, service, _position = _harness(tmp_path)
    state = service._paper["XRPN"]
    state.signal = replace(state.signal, target_levels=())
    before = len(submitter.calls)
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW) is False
        assert len(submitter.calls) == before
    finally:
        writer.close()
        composition.close()


def test_zero_position_does_not_create_target(tmp_path):
    composition, _bridge, submitter, writer, service, position = _harness(tmp_path)
    position["XRPN"] = D("0")
    before = len(submitter.calls)
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW) is False
        assert len(submitter.calls) == before
        assert not tuple(
            o for o in _active_sells(composition)
            if o.request.order_type.value == "LIMIT"
        )
    finally:
        writer.close()
        composition.close()


def test_partial_first_target_fill_reports_durable_remaining_quantity(tmp_path):
    composition, _bridge, submitter, writer, service, position = _harness(tmp_path)
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW)
        target = next(
            o for o in _active_sells(composition)
            if o.request.execution_reason == "FIRST_TARGET"
        )
        _quote(composition, 2, "18.427490", "18.44", "5")
        target = composition.order_book.get(target.order_id)
        assert target.status is OrderStatus.PARTIALLY_FILLED
        assert target.remaining_quantity == D("18")
        position["XRPN"] = D("20")
        before_calls = len(submitter.calls)
        before_events = len(
            performance_diagnostics.reconciliation_metrics()["management_events"]
        )
        result = service.reconcile_authoritative_protection(
            "XRPN", NOW + timedelta(seconds=2),
        )
        assert result, performance_diagnostics.reconciliation_metrics()[
            "management_events"
        ][-8:]
        assert len(submitter.calls) == before_calls
        events = _events_since(before_events)
        complete = next(
            e for e in events if e["state"] == "BRACKET_RECONCILIATION_COMPLETE"
        )
        assert complete["desired_target_qty"] == 18
        assert complete["actual_target_working_qty"] == 18
    finally:
        writer.close()
        composition.close()


def test_full_first_target_completion_advances_to_second_target(tmp_path):
    composition, bridge, submitter, writer, service, position = _harness(tmp_path)
    try:
        assert service.reconcile_authoritative_protection("XRPN", NOW)
        target = next(
            o for o in _active_sells(composition)
            if o.request.execution_reason == "FIRST_TARGET"
        )
        _quote(composition, 2, "18.427490", "18.44", "23")
        target = composition.order_book.get(target.order_id)
        assert target.status is OrderStatus.FILLED
        bridge._reconcile_terminal_exits()
        position["XRPN"] = D("2")
        result = service.reconcile_authoritative_protection(
            "XRPN", NOW + timedelta(seconds=2),
        )
        debug_events = performance_diagnostics.reconciliation_metrics()[
            "management_events"
        ][-8:]
        incomplete = next(
            (e for e in reversed(debug_events)
             if e["state"] == "BRACKET_RECONCILIATION_INCOMPLETE"),
            {},
        )
        assert result, (
            incomplete.get("reason"),
            tuple((item.state.value, item.reason, item.order_id)
                  for item in submitter.results[-3:]),
            bridge._active_by_symbol.get("XRPN"),
            bridge._authoritative_quantity("XRPN"),
            bridge.management_readiness("XRPN").value,
            tuple(
                (
                    o.request.order_type.value,
                    o.request.strategy_lifecycle_id,
                    int(o.remaining_quantity),
                )
                for o in _active_sells(composition)
            ),
        )
        state = service._paper["XRPN"]
        assert state.first_taken is True
        second = tuple(
            o for o in _active_sells(composition)
            if o.request.execution_reason == "SECOND_TARGET"
        )
        assert len(second) == 1
        assert second[0].remaining_quantity == D("1")
        assert second[0].request.limit_price == D("19.294980")
    finally:
        writer.close()
        composition.close()
