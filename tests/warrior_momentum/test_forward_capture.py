from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from dataclasses import replace
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
from app.strategies.warrior_momentum.forward_runtime import management_context_available
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.autonomous_paper import (
    PaperExitSubmissionDecision, PaperExitSubmissionState,
    AutonomousManagementReadiness, AutonomousPaperExecutionBridge,
)
from app.paper_trade_experiment.harness import PaperExperimentJournal
from app.trade_intelligence.decision_intelligence.entry_timing import (
    EntryIntelligenceConfig, HistoricalPaperEntryTimingPolicy, PAPER_TREATMENT,
)
from app.trade_intelligence.decision_intelligence.service import HistoricalDecisionIntelligence
from app.trade_intelligence.taxonomy_paper_bridge import TaxonomyPaperExecutionBridge

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
    decision = store.records(record_type=CaptureRecordType.DECISION)[0].payload
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
    evidence = decision["canonical_setup_evidence"]
    assert evidence["completed_bar_cutoff"] == (T0 + timedelta(minutes=20)).isoformat()
    assert evidence["bar_timestamps"] == [item.timestamp.isoformat() for item in bars()]


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


def test_adaptive_premarket_structural_entry_ignores_old_turnover_proxy(tmp_path: Path) -> None:
    """A valid adaptive re-assessment uses quote safety, not $2.5M turnover."""
    store = ForwardCaptureStore(tmp_path / "adaptive-liquidity.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    submitted = []
    service = WarriorForwardCaptureService(
        store, writer,
        config=WarriorMomentumConfig(adaptive_context_enabled=True),
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    try:
        candidate, signal = service.observe(
            point(
                session="PREMARKET",
                observation=scanner(bid=D("10.00"), ask=D("10.01")),
            ),
            account=account(),
        )
        assert signal is not None
        reassessed_signal = replace(
            signal,
            timestamp=signal.timestamp + timedelta(minutes=1),
            entry_trigger=signal.entry_trigger + D("0.01"),
            stop_price=signal.stop_price + D("0.001"),
        )
        low_turnover = replace(
            candidate, dollar_volume=D("1500000"),
            bid=D("10.18"), ask=D("10.22"),
        )
        assert service.authorize_new_structural_entry(
            low_turnover, reassessed_signal, account(),
            freshness_ok=True, higher_low=True,
        ) is True
        assert len(submitted) == 2
    finally:
        writer.close()


def test_execution_entry_signal_uses_fresh_ask_inside_existing_displacement(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "execution-entry-price.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        value = point()
        candidate = service.runtime.discover(
            value.observation, value.bars, session=value.session,
        )
        candidate, signal = service.runtime.assess_entry(candidate)
        assert signal is not None
        structural = signal.entry_trigger
        executable_ask = structural + D("0.01")
        value = point(observation=scanner(
            bid=executable_ask - D("0.01"), ask=executable_ask,
        ))
        executable = service._execution_entry_signal(value, candidate, signal)
        assert executable is not None
        assert executable.structural_entry_trigger == structural
        assert executable.entry_trigger == executable_ask
        assert executable.risk_per_share == executable_ask - signal.stop_price
        assert executable.target_levels[0] == executable_ask + executable.risk_per_share
    finally:
        writer.close()


def test_execution_entry_signal_refuses_dead_limit_outside_displacement(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "execution-entry-missed.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        value = point()
        candidate = service.runtime.discover(
            value.observation, value.bars, session=value.session,
        )
        candidate, signal = service.runtime.assess_entry(candidate)
        assert signal is not None
        escaped = signal.entry_trigger + D("0.06")
        value = point(observation=scanner(
            bid=escaped - D("0.01"), ask=escaped,
        ))
        assert service._execution_entry_signal(value, candidate, signal) is None
    finally:
        writer.close()


def test_adaptive_rearm_requires_current_quote_safety_after_strategy_signal(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "adaptive-rearm-liquidity.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    rearmed = []
    service = WarriorForwardCaptureService(
        store, writer,
        config=WarriorMomentumConfig(adaptive_context_enabled=True),
        paper_entry_submitter=lambda *_args: True,
        paper_entry_rearmer=lambda *args, **kwargs: rearmed.append((args, kwargs)),
    )
    try:
        candidate, signal = service.observe(
            point(observation=scanner(bid=D("10.00"), ask=D("10.01"))),
            account=account(),
        )
        assert signal is not None
        low_turnover = replace(candidate, dollar_volume=D("1200000"))
        service._consider_fast_momentum_rearm(point(), low_turnover, signal, account())
        assert rearmed
        rearmed.clear()
        invalid_quote = replace(low_turnover, bid=None, ask=None)
        service._consider_fast_momentum_rearm(point(), invalid_quote, signal, account())
        assert not rearmed
    finally:
        writer.close()


def test_taxonomy_trigger_remains_advisory_when_canonical_warrior_is_not_ready(
    tmp_path: Path,
) -> None:
    """A taxonomy trigger cannot create execution authority by itself."""
    store = ForwardCaptureStore(tmp_path / "entry-intelligence.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    experiment = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(
            enabled=True, mode=PAPER_TREATMENT, allocation_percent=0,
            journal_path=None,
        ),
        journal=journal,
    )
    intelligence = HistoricalDecisionIntelligence(
        journal_path=tmp_path / "intelligence.sqlite3",
    )
    intelligence.start("PAPER")
    composition = create_paper_trading_command_composition(at=T0 + timedelta(minutes=20))
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    pretrigger = bars()
    pretrigger = (*pretrigger[:-1], bar(4, "9.96", "10", "9.94", "9.95", "300"))
    try:
        forward = WarriorForwardCaptureService(
            store, writer,
            paper_entry_submitter=bridge.submit_entry,
            taxonomy_execution_bridge=TaxonomyPaperExecutionBridge(),
            decision_intelligence_observer=intelligence.observe_decision,
            paper_entry_intelligence=experiment.assess,
        )
        candidate, signal = forward.observe(
            point(bars=pretrigger), account=account(),
        )
        assert candidate.setup is not None
        assert signal is None
        assert len(composition.order_book.history()) == 0
    finally:
        intelligence.close()
        journal.close()
        writer.close()
        composition.close()


def test_enabled_entry_experiment_control_uses_normal_paper_path(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "control-entry-intelligence.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    journal = PaperExperimentJournal(tmp_path / "control-experiment.sqlite3")
    experiment = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(
            enabled=True, mode=PAPER_TREATMENT, allocation_percent=100,
        ),
        journal=journal,
    )
    intelligence = HistoricalDecisionIntelligence(
        journal_path=tmp_path / "control-intelligence.sqlite3",
    )
    intelligence.start("PAPER")
    composition = create_paper_trading_command_composition(at=T0 + timedelta(minutes=20))
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    forward = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=bridge.submit_entry,
        taxonomy_execution_bridge=TaxonomyPaperExecutionBridge(),
        decision_intelligence_observer=intelligence.observe_decision,
        paper_entry_intelligence=experiment.assess,
    )
    try:
        _candidate, signal = forward.observe(point(), account=account())
        assert signal is not None
        assert len(composition.order_book.history()) == 1
        assignment = journal._connection.execute(
            "SELECT assignment_id, arm FROM experiment_assignments"
        ).fetchone()
        assert assignment[1] == "CONTROL"
        order = composition.order_book.history()[0]
        assert journal.assignment_for_lifecycle(order.request.strategy_lifecycle_id)[0] == assignment[0]
        reports = composition.gateway.process_market_event(MarketEvent(
            1, session_timestamp(1, at=T0 + timedelta(minutes=20)), "XYZ", "control-test",
            MarketEventType.QUOTE,
            QuotePayload(signal.entry_trigger - D("0.01"), signal.entry_trigger,
                         D("1000"), D("1000")),
        ))
        assert reports and reports[0].fills
        assert not any(
            row[0] == "TREATMENT"
            for row in journal._connection.execute(
                "SELECT arm FROM experiment_assignments"
            )
        )
    finally:
        intelligence.close()
        journal.close()
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


def test_recovered_position_can_heal_protection_then_resume_target_management(
    tmp_path: Path,
) -> None:
    """A valid recovered lifecycle can repair protection without staying deadlocked."""
    store = ForwardCaptureStore(tmp_path / "recovered-protection-heal.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("0")}
    context = {"identity": None}
    composition = create_paper_trading_command_composition(
        at=T0 + timedelta(minutes=20),
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
        management_context_source=lambda symbol: (
            context["identity"] if symbol == "XYZ" else None
        ),
    )
    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_entry_submitter=bridge.submit_entry,
        paper_exit_submitter=bridge.ensure_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )

    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        entry = composition.order_book.open_orders()[0]
        identity = entry.request.strategy_lifecycle_id
        assert identity is not None
        context["identity"] = identity

        composition.gateway.process_market_event(MarketEvent(
            1,
            session_timestamp(1, at=T0 + timedelta(minutes=20)),
            "XYZ",
            "recovered-protection-heal",
            MarketEventType.QUOTE,
            QuotePayload(
                signal.entry_trigger - D("0.01"), signal.entry_trigger,
                D("10000"), D("10000"),
            ),
        ))
        shares = int(entry.quantity)
        position["XYZ"] = Decimal(shares)

        # Reproduce the live TJGC seam: execution ownership and durable
        # management context agree, but a prior protection failure left the
        # recovered symbol marked management-incomplete and with no sell order.
        bridge._recovered_symbols.add("XYZ")
        bridge._management_incomplete.add("XYZ")
        assert (
            bridge.management_readiness("XYZ")
            is AutonomousManagementReadiness.RECONCILIATION_REQUIRED
        )
        assert composition.order_book.open_orders_for_symbol("XYZ") == ()

        repaired = bridge.ensure_exit(
            "XYZ", shares, signal.stop_price, "STOP", identity,
        )
        assert repaired.protection_active is True
        assert "XYZ" not in bridge._management_incomplete
        assert (
            bridge.management_readiness("XYZ")
            is AutonomousManagementReadiness.READY
        )

        stops = tuple(
            order for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
            and order.request.order_type.value == "STOP"
        )
        assert len(stops) == 1
        assert int(stops[0].remaining_quantity) == shares
        assert stops[0].request.strategy_lifecycle_id == identity

        # Once the recovered stop is authoritative, the existing target path
        # must be available immediately rather than remaining fail-closed.
        target_quantity = max(1, shares // 3)
        target = bridge.ensure_exit(
            "XYZ", target_quantity, signal.target_levels[0],
            "FIRST_TARGET", identity,
        )
        assert target.protection_active is True
        sells = tuple(
            order for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
        )
        assert {order.request.order_type.value for order in sells} == {"LIMIT", "STOP"}
        assert sum(int(order.remaining_quantity) for order in sells) == shares
    finally:
        writer.close()
        composition.close()


def test_bridge_rebalances_correlated_stop_across_targets_and_runner(tmp_path: Path) -> None:
    """Real PAPER bridge keeps exactly bounded protection through target scales."""
    store = ForwardCaptureStore(tmp_path / "correlated-target-management.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("0")}
    composition = create_paper_trading_command_composition(
        at=T0 + timedelta(minutes=20),
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_entry_submitter=bridge.submit_entry,
        paper_exit_submitter=bridge.ensure_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )

    def paper_quote(sequence: int, bid: Decimal, ask: Decimal) -> None:
        composition.gateway.process_market_event(MarketEvent(
            sequence,
            session_timestamp(sequence, at=T0 + timedelta(minutes=20)),
            "XYZ",
            "correlated-management-test",
            MarketEventType.QUOTE,
            QuotePayload(bid, ask, D("10000"), D("10000")),
        ))

    def open_sells():
        return tuple(
            order for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
        )

    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        shares = int(composition.order_book.open_orders()[0].quantity)

        # Authoritative entry fill, then first management bar establishes stop.
        paper_quote(1, signal.entry_trigger - D("0.01"), signal.entry_trigger)
        position["XYZ"] = Decimal(shares)
        activation_bar = MinuteBar(
            "XYZ",
            signal.timestamp + timedelta(minutes=1),
            signal.entry_trigger,
            signal.entry_trigger,
            signal.entry_trigger,
            signal.entry_trigger,
            D("100"),
        )
        service.observe_market_bar(
            "XYZ", activation_bar, activation_bar.timestamp + timedelta(minutes=1),
        )
        sells = open_sells()
        assert len(sells) == 1
        assert sells[0].request.order_type.value == "STOP"
        assert int(sells[0].remaining_quantity) == shares

        # FIRST_TARGET must coexist with a reduced stop; reservations cannot
        # exceed the authoritative position.
        first_bar = MinuteBar(
            "XYZ",
            signal.timestamp + timedelta(minutes=2),
            signal.entry_trigger,
            signal.target_levels[0] + D("0.01"),
            signal.entry_trigger,
            signal.target_levels[0],
            D("100"),
        )
        service.observe_market_bar(
            "XYZ", first_bar, first_bar.timestamp + timedelta(minutes=1),
        )
        state = service._paper["XYZ"]
        first_quantity = state.first_quantity
        sells = open_sells()
        assert {order.request.order_type.value for order in sells} == {"LIMIT", "STOP"}
        assert sum(int(order.remaining_quantity) for order in sells) == shares
        first_target = next(order for order in sells if order.request.order_type.value == "LIMIT")
        first_stop = next(order for order in sells if order.request.order_type.value == "STOP")
        assert first_target.request.execution_reason == "FIRST_TARGET"
        assert int(first_target.remaining_quantity) == first_quantity
        assert int(first_stop.remaining_quantity) == shares - first_quantity

        paper_quote(2, signal.target_levels[0], signal.target_levels[0] + D("0.01"))
        position["XYZ"] = Decimal(shares - first_quantity)

        # SECOND_TARGET must repeat the same correlated reservation invariant.
        second_bar = MinuteBar(
            "XYZ",
            signal.timestamp + timedelta(minutes=3),
            signal.target_levels[0],
            signal.target_levels[1] + D("0.01"),
            signal.target_levels[0],
            signal.target_levels[1],
            D("100"),
        )
        service.observe_market_bar(
            "XYZ", second_bar, second_bar.timestamp + timedelta(minutes=1),
        )
        state = service._paper["XYZ"]
        assert state.first_taken is True
        second_quantity = state.second_quantity
        remaining_after_first = shares - first_quantity
        sells = open_sells()
        assert {order.request.order_type.value for order in sells} == {"LIMIT", "STOP"}
        assert sum(int(order.remaining_quantity) for order in sells) == remaining_after_first
        second_target = next(order for order in sells if order.request.order_type.value == "LIMIT")
        second_stop = next(order for order in sells if order.request.order_type.value == "STOP")
        assert second_target.request.execution_reason == "SECOND_TARGET"
        assert int(second_target.remaining_quantity) == second_quantity
        assert int(second_stop.remaining_quantity) == remaining_after_first - second_quantity

        paper_quote(3, signal.target_levels[1], signal.target_levels[1] + D("0.01"))
        runner_quantity = shares - first_quantity - second_quantity
        position["XYZ"] = Decimal(runner_quantity)

        # A full runner target replaces the remaining stop with exactly one
        # bounded target for the authoritative remainder.
        runner_bar = MinuteBar(
            "XYZ",
            signal.timestamp + timedelta(minutes=4),
            signal.target_levels[1],
            signal.target_levels[2] + D("0.01"),
            signal.target_levels[1],
            signal.target_levels[2],
            D("100"),
        )
        service.observe_market_bar(
            "XYZ", runner_bar, runner_bar.timestamp + timedelta(minutes=1),
        )
        state = service._paper["XYZ"]
        assert state.second_taken is True
        sells = open_sells()
        assert len(sells) == 1
        assert sells[0].request.order_type.value == "LIMIT"
        assert sells[0].request.execution_reason == "RUNNER_TARGET"
        assert int(sells[0].remaining_quantity) == runner_quantity
        assert bridge.has_execution_ownership("XYZ") is True
    finally:
        writer.close()
        composition.close()


def test_profit_defense_tracks_peak_and_tightens_after_confirmed_giveback(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "profit-defense.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        risk = signal.risk_per_share
        first = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.target_levels[0] + D("0.01"), signal.entry_trigger,
            signal.target_levels[0], D("100"),
        )
        service.observe_market_bar("XYZ", first, first.timestamp)
        assert state.first_taken is True
        peak = signal.entry_trigger + risk * D("1.6")
        giveback = signal.entry_trigger + risk * D("1.3")
        defense = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2), peak, peak,
            signal.entry_trigger + risk * D("1.2"), giveback, D("100"),
        )
        service.observe_market_bar("XYZ", defense, defense.timestamp)
        assert state.peak_price == peak
        assert state.peak_r == D("1.6")
        assert state.profit_defense_armed is True
        assert state.profit_defense_stop_tightened is True
        assert state.stop == giveback
        assert state.second_taken is False
    finally:
        writer.close()


def test_profit_defense_runner_exit_is_limited_and_preserves_milestones(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "profit-defense-runner.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        risk = signal.risk_per_share
        first = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.target_levels[0] + D("0.01"), signal.entry_trigger,
            signal.target_levels[0], D("100"),
        )
        service.observe_market_bar("XYZ", first, first.timestamp)
        second = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2), signal.target_levels[0],
            signal.target_levels[1] + D("0.01"), signal.target_levels[0],
            signal.target_levels[1], D("100"),
        )
        service.observe_market_bar("XYZ", second, second.timestamp)
        assert state.second_taken is True
        peak = signal.entry_trigger + risk * D("2.8")
        service.observe_market_bar(
            "XYZ", MinuteBar(
                "XYZ", signal.timestamp + timedelta(minutes=3), peak, peak,
                signal.entry_trigger + risk * D("2.5"),
                signal.entry_trigger + risk * D("2.7"), D("100"),
            ), signal.timestamp + timedelta(minutes=3),
        )
        current = signal.entry_trigger + risk * D("2.3")
        service.observe_market_bar(
            "XYZ", MinuteBar(
                    "XYZ", signal.timestamp + timedelta(minutes=4), current + D("0.01"),
                        current + D("0.02"), signal.entry_trigger + risk * D("2.1"), current, D("100"),
            ), signal.timestamp + timedelta(minutes=4),
        )
        assert state.peak_r == D("2.8")
        assert state.profit_defense_runner_exit is True
        assert state.first_taken is True and state.second_taken is True
        writer.flush()
        assert any(
            record.payload.get("label") == "PROFIT_DEFENSE_RUNNER_EXIT"
            for record in store.records(record_type=CaptureRecordType.PAPER_FILL)
        )
    finally:
        writer.close()


def test_single_add_on_preserves_parent_milestones_and_exits_leg_only(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "add-on.sqlite3")
    fingerprint = strategy_configuration_fingerprint()
    writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    position = {"XYZ": Decimal("200")}
    exits: list[tuple[int, Decimal, str]] = []

    def submit_exit(symbol, quantity, price, reason, _lifecycle):
        exits.append((quantity, price, reason))
        return True

    service = WarriorForwardCaptureService(
        store, writer,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
        paper_exit_submitter=submit_exit,
        configuration_fingerprint=fingerprint,
    )
    try:
        assessed, signal = service.observe(point(), account=account())
        assert signal is not None and assessed.setup is not None
        state = service._paper["XYZ"]
        state.first_taken = True
        state.second_taken = True
        state.remaining = state.initial_quantity // 2
        state.maximum_high = signal.entry_trigger + signal.risk_per_share * D("3")
        state.peak_r = D("3")

        add_candidate = replace(
            assessed,
            price=signal.entry_trigger + D("0.10"),
            setup=replace(
                assessed.setup, trigger=signal.entry_trigger + D("0.10"),
                stop_price=signal.stop_price,
            ),
        )
        add_signal = service.runtime.entry_signal(add_candidate)
        assert add_signal is not None
        assert service.consider_add_on(
            add_candidate, add_signal, account(), value=point()
        ) is True
        assert state.add_on is not None
        add_on_id = state.add_on.add_on_id
        assert state.add_on.requested_quantity > 0
        assert state.first_taken is True and state.second_taken is True
        assert state.peak_r == D("3")

        assert service.consider_add_on(
            add_candidate, add_signal, account(), value=point()
        ) is False
        assert service.exit_add_on("XYZ", D("10.30")) is True
        add_on_exit = next(item for item in exits if item[2] == "AUTONOMOUS_ADD_ON_EXIT")
        assert add_on_exit[0] == state.add_on.requested_quantity
        assert add_on_exit[0] <= int(position["XYZ"])

        writer.flush()
        restarted_writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
        restarted = WarriorForwardCaptureService(
            store, restarted_writer,
            paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
            configuration_fingerprint=fingerprint,
        )
        restored = restarted._paper["XYZ"]
        assert restored.add_on_used is True
        assert restored.add_on is not None
        assert restored.add_on.add_on_id == add_on_id
        assert restored.first_taken is True and restored.second_taken is True
        restarted_writer.close()
    finally:
        writer.close()


def test_authoritative_fill_establishes_protection_at_actual_quantity(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "actual-fill-protection.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("50")}
    submitted: list[tuple[int, str]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submitted.append((quantity, reason))
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            order_id=f"protect-{len(submitted)}",
            activation_timestamp=T0 + timedelta(minutes=2),
        )

    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), signal.entry_trigger,
            signal.entry_trigger, signal.entry_trigger, signal.entry_trigger, D("100"),
        )
        service.observe_market_bar("XYZ", bar, bar.timestamp + timedelta(minutes=1))
        service.observe_market_bar(
            "XYZ", replace(bar, timestamp=bar.timestamp + timedelta(minutes=1)),
            bar.timestamp + timedelta(minutes=2),
        )
        assert submitted == [(50, "STOP")]
        assert service._paper["XYZ"].remaining == 50
        assert service._paper["XYZ"].protective_stop_activated_at == T0 + timedelta(minutes=2)
    finally:
        writer.close()


def test_authoritative_open_position_targets_continue_after_entry_eligibility_disappears(tmp_path: Path) -> None:
    """Retained PAPER management is independent from current entry qualification."""
    store = ForwardCaptureStore(tmp_path / "retained-management.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("100")}
    submissions: list[tuple[str, int, Decimal]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submissions.append((reason, quantity, price))
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            order_id=f"order-{len(submissions)}",
            activation_timestamp=T0 + timedelta(minutes=21),
        )

    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        # Simulate the post-entry state TJGC exposed: current scanner/Warrior
        # entry eligibility has disappeared, but the authoritative position
        # remains open and must still be managed from retained lifecycle state.
        service.observe(
            point(
                observation=scanner(
                    timestamp=T0 + timedelta(minutes=21),
                    price=D("9.00"), bid=D("8.99"), ask=D("9.01"),
                    current_volume=D("1"),
                ),
                bars=(),
                historical_bars_available=False,
                quote_observed_at=T0 + timedelta(minutes=21),
                last_price_observed_at=T0 + timedelta(minutes=21),
            ),
            account=account(),
        )
        assert service._paper["XYZ"].remaining == 100

        first = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2),
            signal.entry_trigger, signal.target_levels[0] + D("0.01"),
            signal.entry_trigger, signal.target_levels[0], D("100"),
        )
        service.observe_market_bar("XYZ", first, first.timestamp + timedelta(minutes=1))
        assert submissions[0][0] == "STOP"
        assert submissions[1][0] == "FIRST_TARGET"

        first_quantity = service._paper["XYZ"].first_quantity
        position["XYZ"] = Decimal(100 - first_quantity)
        second = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=3),
            signal.target_levels[0], signal.target_levels[1] + D("0.01"),
            signal.target_levels[0], signal.target_levels[1], D("100"),
        )
        service.observe_market_bar("XYZ", second, second.timestamp + timedelta(minutes=1))
        assert service._paper["XYZ"].first_taken is True
        assert submissions[-1][0] == "SECOND_TARGET"

        second_quantity = service._paper["XYZ"].second_quantity
        position["XYZ"] = Decimal(100 - first_quantity - second_quantity)
        runner = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=4),
            signal.target_levels[1], signal.target_levels[2] + D("0.01"),
            signal.target_levels[1], signal.target_levels[2], D("100"),
        )
        service.observe_market_bar("XYZ", runner, runner.timestamp + timedelta(minutes=1))
        assert service._paper["XYZ"].second_taken is True
        assert submissions[-1][0] == "RUNNER_TARGET"
    finally:
        writer.close()


def test_after_hours_management_bar_advances_retained_position(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "after-hours-management.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("100")}
    submissions: list[str] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submissions.append(reason)
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            order_id=f"order-{len(submissions)}",
            activation_timestamp=T0 + timedelta(minutes=2),
        )

    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        after_hours_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2), signal.entry_trigger,
            signal.target_levels[0], signal.entry_trigger, signal.target_levels[0], D("100"),
        )
        service.observe_market_bar(
            "XYZ", after_hours_bar, after_hours_bar.timestamp + timedelta(minutes=1),
        )
        assert submissions == ["STOP", "FIRST_TARGET"]
        assert service._paper["XYZ"].exit_reason == "FIRST_TARGET"
    finally:
        writer.close()


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
    before.maximum_high = signal.entry_trigger + signal.risk_per_share * Decimal("2")
    before.peak_price = before.maximum_high
    before.peak_r = Decimal("2")
    before.current_r = Decimal("1.5")
    before.profit_defense_armed = True
    before.profit_defense_stop_tightened = True
    before.profit_defense_last_action = "PROFIT_DEFENSE_STOP_TIGHTENED"
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
    assert state.maximum_high == signal.entry_trigger + signal.risk_per_share * Decimal("2")
    assert state.peak_price == signal.entry_trigger + signal.risk_per_share * Decimal("2")
    assert state.peak_r == Decimal("2")
    assert state.current_r == D("0.015") / signal.risk_per_share
    assert state.profit_defense_armed is True
    assert state.profit_defense_stop_tightened is True
    assert state.profit_defense_last_action == "PROFIT_DEFENSE_STOP_TIGHTENED"
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


def test_management_context_lookup_is_symbol_local_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "management-readiness.sqlite3"
    store = ForwardCaptureStore(path)
    fingerprint = "current-fingerprint"
    lifecycle = "WARRIOR_MOMENTUM_V1|XYZ|episode"
    records = [
        CaptureRecord.create(
            CaptureRecordType.MANAGEMENT_CONTEXT,
            "NOISE",
            T0 + timedelta(seconds=index),
            {
                "environment": "PAPER",
                "strategy": "WARRIOR_MOMENTUM_V1",
                "lifecycle_id": f"noise-{index}",
                "phase": "MANAGING",
                "stop": "9.50",
                "configuration_fingerprint": fingerprint,
            },
            identity_parts=("noise", str(index)),
        )
        for index in range(600)
    ]
    records.append(CaptureRecord.create(
        CaptureRecordType.MANAGEMENT_CONTEXT,
        "XYZ",
        T0 + timedelta(minutes=20),
        {
            "environment": "PAPER",
            "strategy": "WARRIOR_MOMENTUM_V1",
            "lifecycle_id": lifecycle,
            "phase": "MANAGING",
            "stop": "10.00",
            "configuration_fingerprint": fingerprint,
        },
        identity_parts=("target",),
    ))
    store.append_batch(tuple(records))
    materialized_before = store.records_materialized_total()

    assert management_context_available(
        path, "XYZ", lifecycle_id=lifecycle,
        configuration_fingerprint=fingerprint,
    ) == lifecycle

    assert store.records_materialized_total() == materialized_before
    # Prove the dedicated store boundary itself is bounded and symbol-local.
    latest = store.latest_records_for_symbol(
        symbol="XYZ", record_type=CaptureRecordType.MANAGEMENT_CONTEXT, limit=8,
    )
    assert len(latest) == 1
    assert latest[0].payload["lifecycle_id"] == lifecycle


def test_management_context_lookup_does_not_revive_closed_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "management-closed.sqlite3"
    store = ForwardCaptureStore(path)
    fingerprint = "current-fingerprint"
    lifecycle = "WARRIOR_MOMENTUM_V1|XYZ|episode"
    base = {
        "environment": "PAPER",
        "strategy": "WARRIOR_MOMENTUM_V1",
        "lifecycle_id": lifecycle,
        "stop": "10.00",
        "configuration_fingerprint": fingerprint,
    }
    store.append_batch((
        CaptureRecord.create(
            CaptureRecordType.MANAGEMENT_CONTEXT, "XYZ", T0,
            {**base, "phase": "MANAGING"}, identity_parts=("managing",),
        ),
        CaptureRecord.create(
            CaptureRecordType.MANAGEMENT_CONTEXT, "XYZ", T0 + timedelta(minutes=1),
            {**base, "phase": "CLOSED"}, identity_parts=("closed",),
        ),
    ))

    assert management_context_available(
        path, "XYZ", lifecycle_id=lifecycle,
        configuration_fingerprint=fingerprint,
    ) is None
