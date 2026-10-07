from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from dataclasses import replace
from decimal import Decimal, Decimal as D
from pathlib import Path
from queue import Full
from threading import Event
from time import perf_counter, sleep
from types import SimpleNamespace

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
from app.strategies.warrior_momentum.forward_runtime import (
    _signal_from_entry,
    management_context_available,
)
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.autonomous_paper import (
    PaperExitSubmissionDecision, PaperExitSubmissionFailureReason,
    PaperExitSubmissionState,
    AutonomousManagementReadiness, AutonomousPaperExecutionBridge,
    lifecycle_identity,
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


def test_authoritative_fill_path_is_prospective_bounded_and_flags_gaps(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "forward.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, paper_campaign_id="paper-campaign",
    )
    entry_at = T0 + timedelta(minutes=20)
    event = SimpleNamespace(
        fill=SimpleNamespace(
            symbol="XYZ", side="BUY", quantity=D("10"),
            fill_price=D("10.20"), timestamp=entry_at,
        ),
        order=SimpleNamespace(lifecycle_id="life-1", order_id="order-1"),
        source="paper-gateway", sequence=7,
    )
    service.observe_paper_event(event)
    service.observe(point(
        observation=scanner(timestamp=entry_at + timedelta(seconds=2)),
        quote_observed_at=entry_at + timedelta(seconds=2),
        evaluation_timestamp=entry_at + timedelta(seconds=2),
    ))
    service.observe(point(
        observation=scanner(timestamp=entry_at + timedelta(seconds=10)),
        quote_observed_at=entry_at + timedelta(seconds=10),
        evaluation_timestamp=entry_at + timedelta(seconds=17),
    ))
    service.observe_paper_event(SimpleNamespace(
        fill=SimpleNamespace(
            symbol="XYZ", side="SELL", quantity=D("10"),
            fill_price=D("10.40"), timestamp=entry_at + timedelta(seconds=20),
        ),
        order=SimpleNamespace(lifecycle_id="life-1", order_id="order-2"),
        source="paper-gateway", sequence=8,
    ))
    writer.flush()
    records = store.records(record_type=CaptureRecordType.EXECUTION_PRICE_PATH)
    fills = [record.payload for record in records if record.payload["action"] == "FILL"]
    quotes = [record.payload for record in records if record.payload["action"] == "QUOTE"]
    assert [fill["side"] for fill in fills] == ["BUY", "SELL"]
    assert fills[-1]["remaining_quantity"] == "0"
    assert quotes[-1]["stale_quote"] is True
    assert quotes[-1]["missing_interval"] is True
    writer.close()


def test_observational_capture_handoff_does_not_block_strategy_evaluation(
    tmp_path: Path,
) -> None:
    """Slow evidence persistence must not hold up the decision path."""
    store = ForwardCaptureStore(tmp_path / "async-observation.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, async_observation_records=True,
    )
    original_submit_many = writer.submit_many
    entered = Event()

    def slow_submit(records, *, timeout_seconds=1.0):
        entered.set()
        sleep(0.5)
        original_submit_many(records, timeout_seconds=timeout_seconds)

    writer.submit_many = slow_submit
    started = perf_counter()
    service.observe(
        point(observation=scanner(previous_close=D("10"))),
        account=account(),
    )
    elapsed = perf_counter() - started
    assert elapsed < 0.25
    assert entered.wait(1.0)
    service.close_observation_records()
    writer.close()


def test_execution_path_restart_labels_pre_restart_interval_unavailable(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "forward.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, paper_campaign_id="paper-campaign",
    )
    request = SimpleNamespace(
        strategy_lifecycle_id="life-recovered", side=SimpleNamespace(value="BUY"),
        symbol="XYZ",
    )
    service.restore_execution_lifecycles((SimpleNamespace(
        request=request,
        fills=(SimpleNamespace(timestamp=T0, quantity=D("5")),),
    ),))
    writer.flush()
    recovery = store.records(
        record_type=CaptureRecordType.EXECUTION_PRICE_PATH,
    )[0].payload
    assert recovery["action"] == "RECOVERY"
    assert recovery["historical_path"] == "UNAVAILABLE_BEFORE_RESTART"
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


def test_completed_bar_delivery_is_revision_cached_and_research_delta_only(
    capture,
) -> None:
    store, writer, service = capture
    underlying = service._shadow
    assert underlying is not None
    observed: list[datetime] = []

    class ShadowProbe:
        def active_evaluation_ids(self, symbol: str) -> tuple[str, ...]:
            assert symbol == "XYZ"
            return ("existing-evaluation",)

        def observe_bar_for_evaluations(
            self, value: MinuteBar, evaluation_ids: tuple[str, ...],
        ) -> tuple:
            assert evaluation_ids == ("existing-evaluation",)
            observed.append(value.timestamp)
            return ()

        def observe_rejection(self, *args, **kwargs):
            return underlying.observe_rejection(*args, **kwargs)

        def observe_bar(self, value: MinuteBar):
            return underlying.observe_bar(value)

        def finalize_due(self, timestamp: datetime):
            return underlying.finalize_due(timestamp)

    service._shadow = ShadowProbe()
    first_candidate, first_signal = service.observe(point())
    second_candidate, second_signal = service.observe(point())
    added = bar(5, "10.1", "10.25", "10.0", "10.2", "400")
    service.observe(point(bars=(*bars(), added)))

    assert service.wait_for_completed_bar_research(timeout_seconds=2.0)
    writer.flush()
    assert observed == [item.timestamp for item in (*bars(), added)]
    assert len(set(observed)) == len(observed)
    assert len(store.records(record_type=CaptureRecordType.MINUTE_BAR)) == 6
    assert first_candidate == second_candidate
    assert first_signal == second_signal
    metrics = service.completed_bar_metrics()
    assert metrics.cache_hit >= 1
    assert metrics.cache_miss >= 2
    assert metrics.new_bars_processed == 6
    assert metrics.research_submit == 6
    assert metrics.research_worker.completed == 6
    assert service.close_completed_bar_research(timeout_seconds=2.0)


def test_paper_lifecycle_delta_matches_legacy_full_history_replay(
    tmp_path: Path,
) -> None:
    services = []
    for name in ("legacy", "delta"):
        store = ForwardCaptureStore(tmp_path / f"{name}.sqlite3")
        writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
        service = WarriorForwardCaptureService(store, writer)
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        assert "XYZ" in service.open_paper_symbols
        services.append((store, writer, service, signal))

    legacy_store, legacy_writer, legacy, signal = services[0]
    delta_store, delta_writer, delta, delta_signal = services[1]
    assert signal == delta_signal
    first = MinuteBar(
        "XYZ", signal.timestamp + timedelta(minutes=1),
        signal.entry_trigger,
        signal.target_levels[0],
        signal.stop_price + D("0.01"),
        signal.target_levels[0],
        D("1000"),
    )
    stopped = MinuteBar(
        "XYZ", signal.timestamp + timedelta(minutes=2),
        signal.entry_trigger,
        signal.entry_trigger + D("0.01"),
        signal.entry_trigger - D("0.01"),
        signal.entry_trigger,
        D("1000"),
    )
    event_times = (
        first.timestamp + timedelta(minutes=1),
        stopped.timestamp + timedelta(minutes=1),
    )

    for observed_at, history in zip(
        event_times, ((first,), (first, stopped)), strict=True,
    ):
        for value in history:
            legacy.observe_market_bar("XYZ", value, observed_at)
        delta_value = point(
            observation=scanner(timestamp=observed_at),
            bars=history,
            quote_observed_at=observed_at,
            last_price_observed_at=observed_at,
        )
        snapshot = delta._completed_bar_snapshot(delta_value)
        delta._deliver_completed_bar_deltas("XYZ", snapshot, observed_at)

    legacy_writer.flush()
    delta_writer.flush()
    assert legacy.open_paper_symbols == delta.open_paper_symbols == ()
    for record_type in (
        CaptureRecordType.PAPER_FILL,
        CaptureRecordType.MANAGEMENT_CONTEXT,
        CaptureRecordType.STATE_TRANSITION,
    ):
        legacy_payloads = [
            record.payload_json
            for record in legacy_store.records(record_type=record_type)
        ]
        delta_payloads = [
            record.payload_json
            for record in delta_store.records(record_type=record_type)
        ]
        assert delta_payloads == legacy_payloads

    assert legacy.close_completed_bar_research(timeout_seconds=2.0)
    assert delta.close_completed_bar_research(timeout_seconds=2.0)
    legacy_writer.close()
    delta_writer.close()


def test_production_shaped_completed_bar_fast_path_latency(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "completed-bar-latency.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, async_observation_records=True,
    )
    history = tuple(
        bar(
            index,
            str(D("9.70") + D(index % 5) / D("100")),
            str(D("9.80") + D(index % 5) / D("100")),
            str(D("9.60") + D(index % 5) / D("100")),
            str(D("9.75") + D(index % 5) / D("100")),
            "1000",
        )
        for index in range(120)
    )

    # Build multiple real, active shadow evaluations without adding any new
    # completed bar. Their research work must not run on these quote updates.
    for offset in range(24):
        timestamp = T0 + timedelta(minutes=130, seconds=offset)
        service.observe(point(
            observation=scanner(
                timestamp=timestamp, previous_close=D("10"),
            ),
            bars=history,
            quote_observed_at=timestamp,
            last_price_observed_at=timestamp,
        ))

    entry_at = T0 + timedelta(minutes=130, seconds=30)
    _entry_candidate, entry_signal = service.observe(
        point(
            observation=scanner(timestamp=entry_at),
            quote_observed_at=entry_at,
            last_price_observed_at=entry_at,
        ),
        account=account(),
    )
    assert entry_signal is not None
    assert "XYZ" in service.open_paper_symbols

    boundary_bar = bar(131, "10.18", "10.24", "10.12", "10.20", "1200")
    boundary_at = T0 + timedelta(minutes=133)
    latest = point(
        observation=scanner(
            timestamp=boundary_at, previous_close=D("10"),
        ),
        bars=(*history, boundary_bar),
        quote_observed_at=boundary_at,
        last_price_observed_at=boundary_at,
    )
    service.observe(latest)
    assert "XYZ" in service.open_paper_symbols
    assert service.wait_for_completed_bar_research(timeout_seconds=5.0)

    completed_samples: list[float] = []
    for _ in range(200):
        started = perf_counter()
        snapshot = service._completed_bar_snapshot(latest)
        service._deliver_completed_bar_deltas("XYZ", snapshot, boundary_at)
        completed_samples.append((perf_counter() - started) * 1000.0)

    observe_samples: list[float] = []
    for _ in range(100):
        started = perf_counter()
        service.observe(latest)
        observe_samples.append((perf_counter() - started) * 1000.0)

    def percentile(samples: list[float], quantile: float) -> float:
        ordered = sorted(samples)
        return ordered[round((len(ordered) - 1) * quantile)]

    completed_result = (
        percentile(completed_samples, 0.50),
        percentile(completed_samples, 0.90),
        percentile(completed_samples, 0.99),
        max(completed_samples),
    )
    observe_result = (
        percentile(observe_samples, 0.50),
        percentile(observe_samples, 0.90),
        percentile(observe_samples, 0.99),
        max(observe_samples),
    )
    print(
        "completed_bar_ms="
        f"p50={completed_result[0]:.3f},p90={completed_result[1]:.3f},"
        f"p99={completed_result[2]:.3f},max={completed_result[3]:.3f}; "
        "service_observe_ms="
        f"p50={observe_result[0]:.3f},p90={observe_result[1]:.3f},"
        f"p99={observe_result[2]:.3f},max={observe_result[3]:.3f}"
    )
    assert completed_result[0] < 1.0
    assert completed_result[1] < 5.0
    assert completed_result[2] < 15.0
    assert observe_result[0] < 5.0
    assert observe_result[1] < 15.0
    assert observe_result[2] < 50.0

    assert service.close_completed_bar_research(timeout_seconds=5.0)
    service.close_observation_records()
    writer.close()


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
    candidate, signal = service.observe(
        point(observation=scanner(bid=D("9"), ask=D("11"))), account=account(),
    )
    writer.flush()
    assert signal is None
    assert candidate.status.value == "AWAITING_EXECUTION_DATA"
    assert "EXECUTION_QUALITY_WAIT" in {
        reason.value for reason in candidate.reason_codes
    }
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
                observation=scanner(bid=D('10.01'), ask=D('10.02')),
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
        assert executable.target_levels == signal.target_levels
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
        escaped = signal.entry_trigger + D("0.20")
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
            point(observation=scanner(bid=D('10.01'), ask=D('10.02'))),
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


def test_enabled_entry_experiment_control_uses_normal_paper_path(
    tmp_path: Path, monkeypatch,
) -> None:
    # This fixture proves the legacy control path, so opt out of the PAPER
    # adaptive policy that is enabled by default.
    monkeypatch.setenv("ATLAS_WARRIOR_ADAPTIVE_CONTEXT_ENABLED", "false")
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
    assert {
        item.get("lifecycle_id") for item in fills
    } == {lifecycle_identity(signal)}
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


def test_authoritative_first_protection_bar_defers_structural_exit_until_next_bar(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "deferred-first-bar-defense.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("0")}
    submitted: list[tuple[int, Decimal, str]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submitted.append((quantity, price, reason))
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            order_id=f"exit-{len(submitted)}",
            activation_timestamp=None,
        )

    default_config = WarriorMomentumConfig()
    config = replace(
        default_config,
        trade_management=replace(
            default_config.trade_management,
            initial_stop_volatility_multiplier=D("3"),
        ),
    )
    service = WarriorForwardCaptureService(
        store, writer, config=config,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        assert signal.structural_stop_price is not None
        assert signal.stop_price < signal.structural_stop_price
        state = service._paper["XYZ"]
        position["XYZ"] = Decimal(state.initial_quantity)
        between_stops = (signal.stop_price + signal.structural_stop_price) / 2

        ambiguous = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1),
            signal.structural_stop_price + D("0.01"),
            signal.structural_stop_price + D("0.01"),
            between_stops, between_stops, D("100"),
        )
        service.observe_market_bar(
            "XYZ", ambiguous, ambiguous.timestamp + timedelta(minutes=1),
        )

        # This candle predates established protection ownership. Its completed
        # close cannot retroactively authorize a structural market exit.
        assert [reason for _quantity, _price, reason in submitted] == ["STOP"]
        assert state.exit_reason is None
        assert state.protection_reconciled is True
        assert state.remaining == state.initial_quantity

        confirmed = replace(
            ambiguous,
            timestamp=ambiguous.timestamp + timedelta(minutes=1),
        )
        service.observe_market_bar(
            "XYZ", confirmed, confirmed.timestamp + timedelta(minutes=1),
        )

        assert [reason for _quantity, _price, reason in submitted] == [
            "STOP", "STRUCTURAL_CLOSE_INVALIDATION",
        ]
        assert submitted[-1][0] == state.initial_quantity
        assert submitted[-1][1] == between_stops
        assert state.exit_reason == "STRUCTURAL_CLOSE_INVALIDATION"
        assert state.remaining == state.initial_quantity
    finally:
        writer.close()


def test_authorized_rearm_installs_new_generation_for_forward_management(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "rearm-management-handoff.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store,
        writer,
        config=WarriorMomentumConfig(adaptive_context_enabled=True),
        paper_entry_submitter=lambda *_args: True,
        paper_entry_rearmer=lambda *_args, **_kwargs: True,
    )
    try:
        candidate, original = service.observe(
            point(observation=scanner(bid=D('10.01'), ask=D('10.02'))),
            account=account(),
        )
        assert original is not None
        old_generation = lifecycle_identity(service._paper["XYZ"].signal)
        rearmed = replace(
            original,
            timestamp=original.timestamp + timedelta(seconds=1),
            structural_episode_id="ipdn-new-rearm-generation",
        )

        service._consider_fast_momentum_rearm(
            point(), replace(candidate, dollar_volume=D("1200000")),
            rearmed, account(),
        )

        state = service._paper["XYZ"]
        assert lifecycle_identity(state.signal) == lifecycle_identity(rearmed)
        assert lifecycle_identity(state.signal) != old_generation
        assert state.authoritative_position_seen is False
        assert state.remaining == 0
        assert state.initial_stop == rearmed.stop_price
    finally:
        writer.close()


def test_execution_entry_price_diagnostics_identify_each_bound(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "execution-entry-diagnostics.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        value = point()
        candidate = service.runtime.discover(value.observation, value.bars, session=value.session)
        candidate, signal = service.runtime.assess_entry(candidate)
        assert signal is not None

        def check(ask: Decimal, expected: str) -> None:
            reasons: list[str] = []
            changed = replace(value, observation=scanner(
                bid=ask - D("0.01"), ask=ask,
            ))
            assert service._execution_entry_signal(
                changed, candidate, signal,
                diagnostic=lambda reason, **_details: reasons.append(reason),
            ) is None
            assert reasons == [expected]

        # The fixture trigger is below $3.33, so the percentage envelope is tighter.
        low_signal = replace(signal, entry_trigger=D("2"), structural_entry_trigger=D("2"),
                             stop_price=D("1.90"), risk_per_share=D("0.10"), reference_price=D("2"))
        low_candidate = replace(candidate)
        low_value = replace(value, observation=scanner(bid=D("2.03"), ask=D("2.04")))
        reasons: list[str] = []
        assert service._execution_entry_signal(
            low_value, low_candidate, low_signal,
            diagnostic=lambda reason, **_details: reasons.append(reason),
        ) is None
        assert reasons == ["ENTRY_PRICE_DISPLACED_PERCENT"]

        check(signal.entry_trigger + D("0.60"), "ENTRY_PRICE_DISPLACED_BOTH")
    finally:
        writer.close()


def test_execution_envelope_scales_by_price_with_bounded_outer_limit(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "execution-envelope.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, config=WarriorMomentumConfig(adaptive_context_enabled=False),
    )
    try:
        value = point()
        candidate = service.runtime.discover(value.observation, value.bars, session=value.session)
        candidate, signal = service.runtime.assess_entry(candidate)
        assert signal is not None
        expected = {
            D("2"): D("2.030"), D("5"): D("5.075"),
            D("10"): D("10.150"), D("20"): D("20.300"),
            D("50"): D("50.500"),
        }
        for price, maximum in expected.items():
            details: list[dict[str, object]] = []
            custom = replace(signal, entry_trigger=price, structural_entry_trigger=price,
                             reference_price=price, stop_price=price - D("0.10"),
                             risk_per_share=D("0.10"))
            ask = price + (maximum - price) + D("0.01")
            changed = replace(value, observation=scanner(bid=ask - D("0.01"), ask=ask))
            assert service._execution_entry_signal(
                changed, candidate, custom,
                diagnostic=lambda _reason, **values: details.append(values),
            ) is None
            assert details and details[-1]["effective_maximum"] == maximum
    finally:
        writer.close()


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
        assert state.exit_reason == "FIRST_TARGET"
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

        # Reproduce the live TJGC restart seam through the real recovery
        # boundary. Execution ownership is restored before the asynchronous
        # durable management-context lookup exposes the matching lifecycle.
        recovered_bridge = AutonomousPaperExecutionBridge(
            composition.trading_service,
            composition.order_command_factory,
            order_book=composition.order_book,
            position_quantity_source=lambda symbol: position.get(
                symbol, Decimal("0"),
            ),
            management_context_source=lambda symbol: (
                context["identity"] if symbol == "XYZ" else None
            ),
        )
        context["identity"] = None
        assert recovered_bridge.reconcile().value == "READY"
        assert (
            recovered_bridge.management_readiness("XYZ")
            is AutonomousManagementReadiness.RECOVERED_READY
        )
        assert composition.order_book.open_orders_for_symbol("XYZ") == ()

        # The durable context becomes visible after execution recovery. The
        # bridge must now permit only protection to heal the fail-closed state.
        context["identity"] = identity
        repaired = recovered_bridge.ensure_exit(
            "XYZ", shares, signal.stop_price, "STOP", identity,
        )
        assert repaired.protection_active is True
        assert (
            recovered_bridge.management_readiness("XYZ")
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
        target = recovered_bridge.ensure_exit(
            "XYZ", target_quantity, signal.target_levels[0],
            "FIRST_TARGET", identity,
        )
        assert target.protection_active is True
        sells = tuple(
            order for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
        )
        assert {order.request.order_type.value for order in sells} == {"LIMIT", "STOP"}
        recovered_target = next(
            order for order in sells if order.request.order_type.value == "LIMIT"
        )
        recovered_stop = next(
            order for order in sells if order.request.order_type.value == "STOP"
        )
        assert int(recovered_target.remaining_quantity) == target_quantity
        assert int(recovered_stop.remaining_quantity) == shares
        assert recovered_stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"
        assert recovered_stop.request.metadata["correlated_target_order_id"] == (
            recovered_target.order_id
        )
        assert sum(
            int(order.remaining_quantity) for order in sells
            if order.request.metadata.get("reservation_mode") != "CONTINGENT_OCO"
        ) == target_quantity
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
        assert {order.request.order_type.value for order in sells} == {"LIMIT", "STOP"}
        assert any(int(order.remaining_quantity) == shares for order in sells
                   if order.request.order_type.value == "STOP")

        # FIRST_TARGET reserves its shares while a non-reserving contingent
        # stop retains hard-stop authority for every remaining share.
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
        assert sum(
            int(order.remaining_quantity) for order in sells
            if order.request.metadata.get("reservation_mode") != "CONTINGENT_OCO"
        ) == first_quantity
        first_target = next(order for order in sells if order.request.order_type.value == "LIMIT")
        first_stop = next(order for order in sells if order.request.order_type.value == "STOP")
        assert first_target.request.execution_reason == "FIRST_TARGET"
        assert int(first_target.remaining_quantity) == first_quantity
        assert int(first_stop.remaining_quantity) == shares
        assert first_stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"
        assert first_stop.request.metadata["correlated_target_order_id"] == first_target.order_id
        assert first_stop.request.stop_price == signal.entry_trigger
        assert state.stop == signal.entry_trigger

        paper_quote(2, signal.target_levels[0], signal.target_levels[0] + D("0.01"))
        position["XYZ"] = Decimal(shares - first_quantity)

        # SECOND_TARGET repeats full contingent downside coverage.
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
        assert sum(
            int(order.remaining_quantity) for order in sells
            if order.request.metadata.get("reservation_mode") != "CONTINGENT_OCO"
        ) == second_quantity
        second_target = next(order for order in sells if order.request.order_type.value == "LIMIT")
        second_stop = next(order for order in sells if order.request.order_type.value == "STOP")
        assert second_target.request.execution_reason == "SECOND_TARGET"
        assert int(second_target.remaining_quantity) == second_quantity
        assert int(second_stop.remaining_quantity) == remaining_after_first
        assert second_stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"

        paper_quote(3, signal.target_levels[1], signal.target_levels[1] + D("0.01"))
        runner_quantity = shares - first_quantity - second_quantity
        position["XYZ"] = Decimal(runner_quantity)

        # A full runner target owns the share reservation while a persisted
        # contingent stop retains hard-stop trigger authority for that same
        # lifecycle without independently reserving the shares.
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
        assert len(sells) == 2
        runner_target = next(
            order for order in sells
            if order.request.order_type.value == "LIMIT"
        )
        runner_stop = next(
            order for order in sells
            if order.request.order_type.value == "STOP"
        )
        assert runner_target.request.execution_reason == "RUNNER_TARGET"
        assert int(runner_target.remaining_quantity) == runner_quantity
        assert int(runner_stop.remaining_quantity) == runner_quantity
        assert runner_stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"
        assert (
            runner_stop.request.metadata["correlated_target_order_id"]
            == runner_target.order_id
        )
        assert sum(
            int(order.remaining_quantity)
            for order in sells
            if order.request.metadata.get("reservation_mode")
            != "CONTINGENT_OCO"
        ) == runner_quantity
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
        current = signal.entry_trigger + risk * D("0.7")
        defense = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2), peak, peak,
            signal.entry_trigger + risk * D("0.6"), current, D("100"),
        )
        service.observe_market_bar("XYZ", defense, defense.timestamp)
        assert state.peak_price == peak
        assert state.peak_r == D("1.6")
        assert state.profit_defense_armed is True
        assert state.adaptive_exit_assessment is not None
        assert state.giveback_r is not None
        assert (
            state.giveback_r
            >= state.adaptive_exit_assessment.tighten_giveback_r
        )
        assert state.profit_defense_stop_tightened is True
        assert state.stop == current
        assert state.second_taken is False
    finally:
        writer.close()


def test_sdev_first_target_trailing_floor_updates_canonical_stop(
    tmp_path: Path,
) -> None:
    """The SDEV runner may not claim a 4.56 stop while 4.33 is working."""

    store = ForwardCaptureStore(tmp_path / "sdev-canonical-trailing-stop.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("147")}
    submissions: list[tuple[str, int, Decimal]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle, **_kwargs):
        submissions.append((reason, quantity, price))
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.WORKING,
            symbol,
            lifecycle,
            reason,
            order_id=f"{reason.lower()}-{len(submissions)}",
            activation_timestamp=T0,
        )

    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    try:
        _candidate, original = service.observe(point(), account=account())
        assert original is not None
        signal = replace(
            original,
            entry_trigger=D("4.492245"),
            reference_price=D("4.492245"),
            stop_price=D("4.330"),
            structural_stop_price=D("4.330"),
            risk_per_share=D("0.162245"),
            target_levels=(D("4.654490"), D("4.816735"), D("4.978980")),
        )
        state = service._paper["XYZ"]
        state.signal = signal
        state.entry_price = D("4.4819047619")
        state.initial_quantity = 294
        state.remaining = 147
        state.managed_quantity = 294
        state.first_quantity = 147
        state.second_quantity = 73
        state.first_taken = True
        state.stop = D("4.330")
        state.prior_low = D("4.560")
        state.authoritative_position_seen = True
        state.protection_reconciled = True
        state.protective_stop_activated_at = signal.timestamp
        state.active_exit_role = "SECOND_TARGET"
        state.active_exit_order_id = "second-target-working"
        state.exit_reason = "SECOND_TARGET"
        state.exit_price = D("4.816735")
        submissions.clear()

        bar = MinuteBar(
            "XYZ",
            signal.timestamp + timedelta(minutes=1),
            D("4.60"), D("4.65"), D("4.57"), D("4.60"), D("100"),
        )
        service.observe_market_bar(
            "XYZ", bar, bar.timestamp + timedelta(minutes=1),
        )

        assert submissions == [("STOP", 147, D("4.560"))]
        assert state.stop == D("4.560")
        assert state.active_exit_role == "SECOND_TARGET"
        assert state.active_exit_order_id == "second-target-working"
        assert state.exit_reason == "SECOND_TARGET"
        assert state.exit_price == D("4.816735")
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("entry", "peak_bid", "stop"),
    [
        (D("1.760"), D("1.760"), D("1.690")),
        (D("3.930"), D("3.910"), D("3.630")),
    ],
    ids=("olox-no-executable-profit", "aixi-no-executable-profit"),
)
def test_session_losses_do_not_fabricate_profit_defense(
    tmp_path: Path, entry: Decimal, peak_bid: Decimal, stop: Decimal,
) -> None:
    """OLOX/AIXI never had positive executable MFE; defense stays honest."""

    store = ForwardCaptureStore(tmp_path / f"no-profit-{entry}.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        state.entry_price = entry
        state.stop = stop
        state.peak_price = peak_bid
        state.peak_r = max(D("0"), (peak_bid - entry) / (entry - stop))
        state.current_r = state.peak_r
        state.remaining = max(1, state.remaining)
        bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1), peak_bid,
            peak_bid, stop, peak_bid, D("100"),
        )

        assert service._profit_defense_action(state, bar) is None
        assert state.profit_defense_armed is False
    finally:
        writer.close()


def test_pcvx_failed_target_does_not_claim_pending_or_starve_stop_tightening(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "pcvx-target-failure-defense.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("67")}
    submissions: list[tuple[str, int, Decimal]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle, **_kwargs):
        submissions.append((reason, quantity, price))
        if reason == "FIRST_TARGET":
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.UNAVAILABLE,
                symbol,
                lifecycle,
                reason,
                failure_reason=(
                    PaperExitSubmissionFailureReason.MANAGEMENT_NOT_READY
                ),
            )
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.WORKING,
            symbol,
            lifecycle,
            reason,
            order_id=f"stop-{len(submissions)}",
            activation_timestamp=T0,
        )

    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    try:
        _candidate, original = service.observe(point(), account=account())
        assert original is not None
        signal = replace(
            original,
            entry_trigger=D("72.56"),
            reference_price=D("72.56"),
            stop_price=D("72.24"),
            structural_stop_price=D("72.24"),
            risk_per_share=D("0.32"),
            target_levels=(D("72.8325"), D("73.20"), D("74.00")),
        )
        state = service._paper["XYZ"]
        state.signal = signal
        state.entry_price = D("72.49")
        state.initial_quantity = 67
        state.remaining = 67
        state.managed_quantity = 67
        state.first_quantity = 33
        state.second_quantity = 16
        state.stop = D("72.24")
        state.authoritative_position_seen = True
        state.protection_reconciled = True
        state.protective_stop_activated_at = signal.timestamp
        submissions.clear()

        crossed = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1),
            D("72.70"), D("72.90"),
            D("72.60"), D("72.84"), D("100"),
        )
        service.observe_market_bar(
            "XYZ", crossed, crossed.timestamp + timedelta(minutes=1),
        )
        assert any(
            reason == "FIRST_TARGET" and quantity == 33
            and price == D("72.8325")
            for reason, quantity, price in submissions
        )
        assert sum(reason == "FIRST_TARGET" for reason, *_ in submissions) == 1
        assert state.exit_reason is None
        assert state.exit_price is None
        assert state.active_exit_role != "FIRST_TARGET"

        peak = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2),
            D("73.20"), D("73.79"),
            D("73.15"), D("73.70"), D("100"),
        )
        service.observe_market_bar(
            "XYZ", peak, peak.timestamp + timedelta(minutes=1),
        )
        defense = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=3),
            D("73.56"), D("73.60"),
            D("72.94"), D("73.11"), D("100"),
        )
        service.observe_market_bar(
            "XYZ", defense, defense.timestamp + timedelta(minutes=1),
        )

        assert submissions[-1] == ("STOP", 67, D("73.11"))
        assert state.stop == D("73.11")
        assert state.profit_defense_stop_tightened is True
        assert state.profit_defense_last_action == (
            "PROFIT_DEFENSE_STOP_TIGHTENED"
        )
        assert state.exit_reason is None
    finally:
        writer.close()


def test_ipdn_rearmed_generation_harvests_executable_profit_and_locks_runner(
    tmp_path: Path,
) -> None:
    """IPDN: a fresh rearm must be managed, harvested, and protected durably."""

    store = ForwardCaptureStore(tmp_path / "ipdn-profit-harvest.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("0")}
    composition = create_paper_trading_command_composition(
        at=T0 + timedelta(minutes=20),
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_exit_submitter=bridge.ensure_exit,
        paper_position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )

    def market(sequence: int, bid: str, ask: str) -> None:
        composition.gateway.process_market_event(MarketEvent(
            sequence,
            session_timestamp(sequence, at=T0 + timedelta(minutes=20)),
            "XYZ",
            "ipdn-profit-harvest-test",
            MarketEventType.QUOTE,
            QuotePayload(D(bid), D(ask), D("10000"), D("10000")),
        ))

    try:
        candidate = service.runtime.discover(
            point().observation, point().bars, session="REGULAR",
        )
        _candidate, original = service.runtime.assess_entry(candidate)
        assert original is not None
        signal = replace(
            original,
            timestamp=T0 + timedelta(minutes=20),
            entry_trigger=D("4.472235"),
            reference_price=D("4.472235"),
            stop_price=D("4.365"),
            structural_stop_price=D("4.365"),
            risk_per_share=D("0.107235"),
            target_levels=(D("4.579470"), D("4.686705"), D("4.793940")),
            structural_episode_id="ipdn-rearm-generation",
        )
        risk_budget = D("47.290635")
        entry = bridge.submit_entry(signal, 441, risk_budget)
        assert entry is True
        service._install_rearmed_paper_state(
            signal, 441, risk_budget, signal.timestamp,
        )
        market(1, "4.450", "4.458")
        buy = next(
            order for order in composition.order_book.history()
            if order.request.side.value == "BUY"
        )
        assert int(buy.filled_quantity) == 441
        assert buy.average_fill_price == D("4.458")
        position["XYZ"] = D("441")
        lifecycle = lifecycle_identity(signal)
        initial_stop = bridge.ensure_exit(
            "XYZ", 441, D("4.365"), "STOP", lifecycle,
        )
        assert initial_stop.protection_active is True
        first_target = bridge.ensure_exit(
            "XYZ", 220, signal.target_levels[0], "FIRST_TARGET", lifecycle,
        )
        assert first_target.protection_active is True

        profitable_at = signal.timestamp + timedelta(seconds=5)
        value = point(
            observation=scanner(
                timestamp=profitable_at, price=D("5.00"),
                bid=D("4.99"), ask=D("5.00"), previous_close=D("3.00"),
            ),
            quote_observed_at=profitable_at,
            last_price_observed_at=profitable_at,
            evaluation_timestamp=profitable_at,
        )
        service.observe(value)
        state = service._paper["XYZ"]
        assert state.entry_price == D("4.458")
        assert state.peak_executable_bid == D("4.99")
        assert state.peak_executable_pnl == D("234.612")
        assert state.peak_executable_r == (
            D("4.99") - D("4.458")
        ) / (D("4.458") - D("4.365"))
        assert state.pending_profit_harvest_role == "PROFIT_HARVEST_1"
        assert state.stop > state.entry_price

        open_sells = tuple(
            order for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
        )
        harvest = next(
            order for order in open_sells
            if order.request.execution_reason == "PROFIT_HARVEST_1"
        )
        assert not any(
            order.request.execution_reason == "FIRST_TARGET"
            for order in open_sells
        )
        canonical_stop = next(
            order for order in open_sells
            if order.request.order_type.value == "STOP"
        )
        assert int(harvest.remaining_quantity) == 110
        assert int(canonical_stop.remaining_quantity) == 441
        assert canonical_stop.request.stop_price == state.stop

        # A second callback for the same state cannot create a duplicate SELL.
        service.observe(value)
        assert len([
            order for order in composition.order_book.history()
            if order.request.execution_reason == "PROFIT_HARVEST_1"
        ]) == 1

        market(2, "5.00", "5.01")
        harvest = next(
            order for order in composition.order_book.history()
            if order.order_id == harvest.order_id
        )
        assert int(harvest.filled_quantity) == 110
        position["XYZ"] = D("331")
        follow_at = profitable_at + timedelta(seconds=2)
        service.observe(point(
            observation=scanner(
                timestamp=follow_at, price=D("5.31"),
                bid=D("5.30"), ask=D("5.31"), previous_close=D("3.00"),
            ),
            quote_observed_at=follow_at,
            last_price_observed_at=follow_at,
            evaluation_timestamp=follow_at,
        ))
        state = service._paper["XYZ"]
        assert state.profit_harvest_stage >= 1
        assert state.realized_from_partials > D("0")
        assert state.stop > state.entry_price
        assert state.current_secured_profit > D("0")
        assert all(
            int(order.remaining_quantity) <= 331
            for order in composition.order_book.open_orders_for_symbol("XYZ")
            if order.request.side.value == "SELL"
        )
        writer.flush()
        entry_record = next(
            record for record in store.records()
            if record.record_type is CaptureRecordType.PAPER_FILL
            and record.payload.get("action") == "ENTRY"
            and record.payload.get("lifecycle_id") == lifecycle
        )
        # Restart/recovery must be able to rebuild this exact rearmed
        # generation from the immutable authoritative fill rather than
        # adopting an unrelated same-symbol management context.
        for required in (
            "session", "momentum_score", "setup", "entry_trigger",
            "fill_price", "structural_stop", "stop_model",
            "risk_per_share", "targets", "catalyst_state",
            "relative_volume", "spread_percent",
        ):
            assert required in entry_record.payload
        from app.strategies.warrior_momentum.forward_runtime import (
            _signal_from_entry,
        )
        recovered_signal = _signal_from_entry(
            entry_record, entry_record.payload,
        )
        assert lifecycle_identity(recovered_signal) == lifecycle
        assert recovered_signal.stop_price == signal.stop_price
    finally:
        writer.close()
        composition.close()


def test_profit_lock_amend_failure_does_not_claim_projected_protection(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "profit-lock-failure.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": D("441")}
    submissions: list[tuple[str, int, Decimal]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle, **_kwargs):
        submissions.append((reason, quantity, price))
        if reason == "STOP":
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.UNAVAILABLE,
                symbol,
                lifecycle,
                reason,
                failure_reason=(
                    PaperExitSubmissionFailureReason.PROTECTION_RECONCILIATION_FAILED
                ),
            )
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.WORKING,
            symbol,
            lifecycle,
            reason,
            order_id="harvest-working",
            activation_timestamp=T0,
        )

    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position[symbol],
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        state.entry_price = D("4.458")
        state.initial_stop = D("4.2258")
        state.stop = D("4.2258")
        state.initial_quantity = 441
        state.managed_quantity = 441
        state.remaining = 441
        state.authoritative_position_seen = True
        state.protection_reconciled = True
        state.protective_stop_activated_at = signal.timestamp
        prior_stop = state.stop
        submissions.clear()

        at = signal.timestamp + timedelta(seconds=2)
        service._capture_exit_evidence(state, point(
            observation=scanner(
                timestamp=at, price=D("5.31"), bid=D("5.30"), ask=D("5.31"),
            ),
            quote_observed_at=at,
            last_price_observed_at=at,
            evaluation_timestamp=at,
        ))
        assert service._update_executable_profit_state(state) is True
        service._manage_executable_profit_harvest(state, at)

        assert any(reason == "PROFIT_HARVEST_1" for reason, *_ in submissions)
        assert any(reason == "STOP" for reason, *_ in submissions)
        assert state.stop == prior_stop
        assert state.profit_defense_stop_tightened is False
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
        current = signal.entry_trigger + risk * D("1.6")
        service.observe_market_bar(
            "XYZ", MinuteBar(
                    "XYZ", signal.timestamp + timedelta(minutes=4), current + D("0.01"),
                        current + D("0.02"), signal.entry_trigger + risk * D("1.4"), current, D("100"),
            ), signal.timestamp + timedelta(minutes=4),
        )
        assert state.peak_r == D("2.8")
        assert state.adaptive_exit_assessment is not None
        assert state.giveback_r is not None
        assert (
            state.giveback_r
            >= state.adaptive_exit_assessment.runner_exit_giveback_r
        )
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
        assert submitted == [(50, "STOP"), (25, "FIRST_TARGET")]
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
        assert submissions[:2] == ["STOP", "FIRST_TARGET"]
        assert submissions[-1] in {"FIRST_TARGET", "STOP"}
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


def test_authoritative_open_position_recovers_across_paper_campaigns(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "cross-campaign-recovery.sqlite3")
    fingerprint = strategy_configuration_fingerprint()
    writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    original = WarriorForwardCaptureService(
        store, writer,
        configuration_fingerprint=fingerprint,
        paper_campaign_id="campaign-a",
    )
    _candidate, signal = original.observe(point(), account=account())
    assert signal is not None
    shares = original._paper["XYZ"].remaining
    safe_bar = MinuteBar(
        "XYZ", signal.timestamp + timedelta(minutes=1),
        signal.entry_trigger, signal.entry_trigger,
        signal.entry_trigger, signal.entry_trigger, D("100"),
    )
    original.observe_market_bar(
        "XYZ", safe_bar, safe_bar.timestamp + timedelta(minutes=1),
    )
    writer.flush()
    writer.close()

    restarted_writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    restarted = WarriorForwardCaptureService(
        store, restarted_writer,
        configuration_fingerprint=fingerprint,
        paper_campaign_id="campaign-b",
        paper_entry_submitter=lambda *_args: True,
        paper_position_quantity_source=lambda symbol: (
            Decimal(shares) if symbol == "XYZ" else Decimal("0")
        ),
    )
    try:
        assert restarted.open_paper_symbols == ("XYZ",)
        state = restarted._paper["XYZ"]
        assert state.remaining == shares
        assert state.authoritative_position_seen is True
        assert lifecycle_identity(state.signal) == lifecycle_identity(signal)
    finally:
        restarted_writer.close()


def test_recovered_entry_preserves_persisted_lifecycle_identity(
    tmp_path: Path,
) -> None:
    """Recovery must retain execution ownership instead of recomputing identity."""
    store = ForwardCaptureStore(tmp_path / "recovered-identity.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)

    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        writer.flush()
        entry = next(
            item
            for item in store.records(record_type=CaptureRecordType.PAPER_FILL)
            if item.payload.get("action") == "ENTRY"
        )
        persisted = (
            "WARRIOR_MOMENTUM_V1|XYZ|LEGACY_EPISODE|"
            "recovered-execution-owner"
        )
        payload = dict(entry.payload)
        payload["lifecycle_id"] = persisted

        restored = _signal_from_entry(entry, payload)

        assert lifecycle_identity(restored) == persisted
    finally:
        writer.close()


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


def _persisted_recovery_entry(
    symbol: str, lifecycle: str, at: datetime, *, quantity: int,
    entry: str, stop: str, session: str | None,
    include_strategy_context: bool = True,
) -> CaptureRecord:
    entry_value = D(entry)
    stop_value = D(stop)
    risk = entry_value - stop_value
    payload = {
        "action": "ENTRY",
        "entry_authority": "AUTHORITATIVE_PAPER_LEDGER",
        "setup": "HIGH_OF_DAY_BREAKOUT",
        "lifecycle_id": lifecycle,
        "entry_trigger": entry_value,
        "fill_price": entry_value,
        "structural_stop": stop_value,
        "stop_model": "BREAKOUT_LEVEL",
        "risk_per_share": risk,
        "planned_shares": quantity,
        "filled_shares": quantity,
        "risk_dollars": risk * quantity,
        "targets": (entry_value + risk, entry_value + risk * 2, entry_value + risk * 3),
        "authority": "AUTHORITATIVE_PAPER_LEDGER",
    }
    if include_strategy_context:
        payload.update({
            "momentum_score": D("52.48"),
            "catalyst_state": "TRUE",
            "relative_volume": D("3.2"),
        })
    if session is not None:
        payload["session"] = session
    return CaptureRecord.create(
        CaptureRecordType.PAPER_FILL, symbol, at, payload,
        identity_parts=("ENTRY", lifecycle, "RECOVERY_FIXTURE"),
    )


def _persisted_recovery_context(
    symbol: str, lifecycle: str, at: datetime, *, quantity: int,
    entry: str, stop: str, session: str | None = None,
    peak_bid: str | None = None, active_order: str | None = None,
    peak_pnl: str = "0", peak_r: str | None = None,
    profit_defense_armed: bool = False,
    structural_stop: str | None = None,
) -> CaptureRecord:
    payload = {
        "environment": "PAPER",
        "strategy": "WARRIOR_MOMENTUM_V1",
        "lifecycle_id": lifecycle,
        "setup": "HIGH_OF_DAY_BREAKOUT",
        "planned_entry": D(entry),
        "structural_stop": D(structural_stop or stop),
        "initial_stop": D(structural_stop or stop),
        "stop": D(stop),
        "remaining": quantity,
        "managed_quantity": quantity,
        "authoritative_position_seen": True,
        "phase": "MANAGING",
        "peak_executable_bid": None if peak_bid is None else D(peak_bid),
        "peak_executable_pnl": D(peak_pnl),
        "peak_executable_r": None if peak_r is None else D(peak_r),
        "profit_defense_armed": profit_defense_armed,
        "active_exit_role": None if active_order is None else "SECOND_TARGET",
        "active_exit_order_id": active_order,
        "momentum_score": D("52.48"),
        "catalyst_state": "TRUE",
        "relative_volume": D("3.2"),
    }
    if session is not None:
        payload["session"] = session
    return CaptureRecord.create(
        CaptureRecordType.MANAGEMENT_CONTEXT, symbol, at, payload,
        identity_parts=(lifecycle, "MANAGING", at.isoformat()),
    )


def _append_recovery_records(
    store: ForwardCaptureStore, fingerprint: str,
    records: tuple[CaptureRecord, ...],
) -> None:
    store.append_batch(tuple(
        record.with_configuration_fingerprint(fingerprint)
        for record in records
    ))


def test_modern_entry_recovery_preserves_exact_session(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "modern-session.sqlite3")
    fingerprint = strategy_configuration_fingerprint()
    lifecycle = "WARRIOR_MOMENTUM_V1|MODERN|LEGACY_EPISODE|modern"
    entry = _persisted_recovery_entry(
        "MODERN", lifecycle, T0, quantity=10, entry="5.00", stop="4.80",
        session="PREMARKET",
    )
    context = _persisted_recovery_context(
        "MODERN", lifecycle, T0 + timedelta(seconds=1), quantity=10,
        entry="5.00", stop="4.80", session="REGULAR",
    )
    _append_recovery_records(store, fingerprint, (entry, context))
    writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    service = WarriorForwardCaptureService(
        store, writer, configuration_fingerprint=fingerprint,
    )
    try:
        assert service._paper["MODERN"].signal.session == "PREMARKET"
    finally:
        writer.close()


def test_legacy_session_recovers_from_same_generation_context(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "legacy-context-session.sqlite3")
    fingerprint = strategy_configuration_fingerprint()
    lifecycle = "WARRIOR_MOMENTUM_V1|LEGACY|LEGACY_EPISODE|context"
    entry = _persisted_recovery_entry(
        "LEGACY", lifecycle, T0, quantity=12, entry="5.00", stop="4.80",
        session=None, include_strategy_context=False,
    )
    context = _persisted_recovery_context(
        "LEGACY", lifecycle, T0 + timedelta(seconds=1), quantity=12,
        entry="5.00", stop="4.80", session="AFTER_HOURS",
    )
    _append_recovery_records(store, fingerprint, (entry, context))
    writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    service = WarriorForwardCaptureService(
        store, writer, configuration_fingerprint=fingerprint,
    )
    try:
        assert service.open_paper_symbols == ("LEGACY",)
        assert lifecycle_identity(service._paper["LEGACY"].signal) == lifecycle
        assert service._paper["LEGACY"].signal.session == "AFTER_HOURS"
        writer.flush()
        assert any(
            item.payload.get("result") == "LEGACY_SESSION_RECOVERED"
            for item in store.records(record_type=CaptureRecordType.DATA_QUALITY)
        )
    finally:
        writer.close()


def test_unrecoverable_legacy_lifecycle_does_not_block_valid_recovery(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "mixed-session-recovery.sqlite3")
    fingerprint = strategy_configuration_fingerprint()
    bad_lifecycle = "WARRIOR_MOMENTUM_V1|BAD|LEGACY_EPISODE|bad"
    good_lifecycle = "WARRIOR_MOMENTUM_V1|GOOD|LEGACY_EPISODE|good"
    bad_entry = _persisted_recovery_entry(
        "BAD", bad_lifecycle, T0, quantity=20, entry="4.00", stop="3.80",
        session=None, include_strategy_context=False,
    )
    bad_context = _persisted_recovery_context(
        "BAD", bad_lifecycle, T0 + timedelta(seconds=1), quantity=20,
        entry="4.00", stop="3.80",
    )
    good_entry = _persisted_recovery_entry(
        "GOOD", good_lifecycle, T0 + timedelta(seconds=2), quantity=30,
        entry="6.00", stop="5.80", session="REGULAR",
    )
    good_context = _persisted_recovery_context(
        "GOOD", good_lifecycle, T0 + timedelta(seconds=3), quantity=30,
        entry="6.00", stop="5.80",
    )
    _append_recovery_records(
        store, fingerprint, (bad_entry, bad_context, good_entry, good_context),
    )
    writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    service = WarriorForwardCaptureService(
        store, writer, configuration_fingerprint=fingerprint,
    )
    try:
        assert service.open_paper_symbols == ("GOOD",)
        assert lifecycle_identity(service._paper["GOOD"].signal) == good_lifecycle
        writer.flush()
        failures = [
            item.payload for item in store.records(
                record_type=CaptureRecordType.DATA_QUALITY,
            )
            if item.symbol == "BAD" and item.payload.get("action") == "RECOVERY"
        ]
        assert failures[-1]["result"] == "LEGACY_SESSION_UNRECOVERABLE"
        assert failures[-1]["reason"] == "RECOVERY_LIFECYCLE_SKIPPED"
    finally:
        writer.close()


def test_aifa_xndu_legacy_restart_restores_management_and_streaming(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "aifa-xndu-recovery.sqlite3")
    fingerprint = strategy_configuration_fingerprint()
    aifa_lifecycle = (
        "WARRIOR_MOMENTUM_V1|AIFA|LEGACY_EPISODE|"
        "5add12d54b23738fc8a6a4e3b6902c7c7d4132e8162561610addc2b94588e092"
    )
    xndu_lifecycle = (
        "WARRIOR_MOMENTUM_V1|XNDU|LEGACY_EPISODE|"
        "1842c8158cf4ca14d55a2d630667c3ec0dad961d975c19ca9c4e6677e463804d"
    )
    aifa_source = _persisted_recovery_entry(
        "AIFA", aifa_lifecycle, T0, quantity=126,
        entry="7.8378", stop="7.52", session="REGULAR",
    )
    aifa_legacy = _persisted_recovery_entry(
        "AIFA", aifa_lifecycle, T0 + timedelta(seconds=1), quantity=126,
        entry="7.8378", stop="7.52", session=None,
        include_strategy_context=False,
    )
    aifa_context = _persisted_recovery_context(
        "AIFA", aifa_lifecycle, T0 + timedelta(seconds=2), quantity=63,
        entry="7.8378", stop="7.903950", peak_bid="8.16",
        peak_pnl="40.60", peak_r="1.013986", profit_defense_armed=True,
        active_order="PAPER-AIFA-SECOND-TARGET",
        structural_stop="7.52",
    )
    xndu_source = _persisted_recovery_entry(
        "XNDU", xndu_lifecycle, T0 + timedelta(seconds=3), quantity=338,
        entry="5.31", stop="5.17", session="REGULAR",
    )
    xndu_legacy = _persisted_recovery_entry(
        "XNDU", xndu_lifecycle, T0 + timedelta(seconds=4), quantity=338,
        entry="5.31", stop="5.17", session=None,
        include_strategy_context=False,
    )
    xndu_context = _persisted_recovery_context(
        "XNDU", xndu_lifecycle, T0 + timedelta(seconds=5), quantity=338,
        entry="5.31", stop="5.17", peak_bid="5.31",
    )
    _append_recovery_records(store, fingerprint, (
        aifa_source, aifa_legacy, aifa_context,
        xndu_source, xndu_legacy, xndu_context,
    ))
    writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    service = WarriorForwardCaptureService(
        store, writer, configuration_fingerprint=fingerprint,
        paper_campaign_id="campaign-after-restart",
        paper_position_quantity_source=lambda symbol: D("63") if symbol == "AIFA" else D("338"),
    )
    try:
        assert service.open_paper_symbols == ("AIFA", "XNDU")
        aifa = service._paper["AIFA"]
        xndu = service._paper["XNDU"]
        assert lifecycle_identity(aifa.signal) == aifa_lifecycle
        assert lifecycle_identity(xndu.signal) == xndu_lifecycle
        assert aifa.remaining == 63 and xndu.remaining == 338
        assert aifa.peak_executable_bid == D("8.16")
        assert aifa.profit_defense_armed is True
        assert aifa.stop == D("7.903950")
        assert aifa.active_exit_order_id == "PAPER-AIFA-SECOND-TARGET"
        assert xndu.peak_executable_bid == D("5.31")
        assert xndu.profit_defense_armed is False
        assert xndu.stop == D("5.17")

        observed_at = T0 + timedelta(minutes=20)
        service.observe(point(
            observation=scanner(
                symbol="AIFA", timestamp=observed_at, price=D("8.20"),
                bid=D("8.20"), ask=D("8.22"),
            ),
            bars=(), session="REGULAR", quote_observed_at=observed_at,
            last_price_observed_at=observed_at,
            evaluation_timestamp=observed_at,
        ))
        service.observe(point(
            observation=scanner(
                symbol="XNDU", timestamp=observed_at, price=D("5.25"),
                bid=D("5.25"), ask=D("5.27"),
            ),
            bars=(), session="REGULAR", quote_observed_at=observed_at,
            last_price_observed_at=observed_at,
            evaluation_timestamp=observed_at,
        ))
        assert service._paper["AIFA"].peak_executable_bid == D("8.20")
        assert service._paper["AIFA"].latest_exit_evidence is not None
        assert service._paper["XNDU"].latest_exit_evidence is not None
        assert service._paper["XNDU"].stop == D("5.17")
        assert len(service.open_paper_symbols) == 2
    finally:
        writer.close()


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


def test_daily_report_ignores_incomplete_analytical_exit(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "malformed-report.sqlite3")
    store.append_batch((CaptureRecord.create(
        CaptureRecordType.STATE_TRANSITION,
        "MALFORMED",
        T0,
        {"to": ForwardTransition.PAPER_EXIT, "mae_r": "-0.4", "mfe_r": "1.8"},
        identity_parts=("missing-realized",),
    ),))

    report = build_daily_report(store, T0.date())

    assert report.paper_trades == 0
    assert report.total_r is None


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


def test_capture_writer_reports_diagnostic_loss_separately_from_critical_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    writer = ForwardCaptureWriter(
        ForwardCaptureStore(tmp_path / "split-health.sqlite3"),
        flush_interval_seconds=0.01,
    )
    record = CaptureRecord.create(
        CaptureRecordType.LATENCY_DIAGNOSTIC, "XYZ", T0,
        {"diagnostic_kind": "forced_overflow"},
    )

    def full(_record) -> None:
        raise Full

    monkeypatch.setattr(writer._diagnostic_queue, "put_nowait", full)
    assert writer.submit_diagnostic(record) is False
    metrics = writer.metrics()
    assert metrics.diagnostic_dropped_records == 1
    assert metrics.dropped_records == 0
    assert metrics.critical_failure_count == 0
    assert metrics.critical_failure_state is False
    monkeypatch.undo()
    writer.close()


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


def test_pending_first_target_retry_preserves_partial_quantity(
    tmp_path: Path,
) -> None:
    """A working partial target must never be retried as a full-position exit."""

    store = ForwardCaptureStore(tmp_path / "pending-target-quantity.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("98")}
    submissions: list[tuple[str, int]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submissions.append((reason, quantity))
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            order_id=f"order-{len(submissions)}",
            activation_timestamp=T0 + timedelta(minutes=1),
        )

    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        first_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1),
            signal.entry_trigger, signal.target_levels[0] + D("0.01"),
            signal.entry_trigger, signal.target_levels[0], D("100"),
        )
        service.observe_market_bar(
            "XYZ", first_bar, first_bar.timestamp + timedelta(minutes=1),
        )
        first_quantity = service._paper["XYZ"].first_quantity
        assert 0 < first_quantity < position["XYZ"]
        assert submissions[-1] == ("STOP", int(position["XYZ"]))

        followup = replace(first_bar, timestamp=first_bar.timestamp + timedelta(minutes=1))
        service.observe_market_bar(
            "XYZ", followup, followup.timestamp + timedelta(minutes=1),
        )
        assert submissions[-1] == ("STOP", int(position["XYZ"]))

        retry_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2),
            signal.target_levels[0], signal.target_levels[0] + D("0.02"),
            signal.target_levels[0], signal.target_levels[0], D("100"),
        )
        service.observe_market_bar(
            "XYZ", retry_bar, retry_bar.timestamp + timedelta(minutes=1),
        )
        assert submissions[-1] == ("STOP", int(position["XYZ"]))
    finally:
        writer.close()


def test_unfilled_first_target_reversal_protects_full_position_at_break_even(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "unfilled-target-reversal.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": Decimal("100")}
    submissions: list[tuple[str, int, Decimal]] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submissions.append((reason, quantity, price))
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            order_id=f"order-{len(submissions)}",
            activation_timestamp=T0 + timedelta(minutes=1),
        )

    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *_args: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    try:
        _candidate, signal = service.observe(point(), account=account())
        assert signal is not None
        target_bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1),
            signal.entry_trigger, signal.target_levels[0] + D("0.01"),
            signal.entry_trigger, signal.target_levels[0], D("100"),
        )
        service.observe_market_bar(
            "XYZ", target_bar, target_bar.timestamp + timedelta(minutes=1),
        )
        state = service._paper["XYZ"]
        assert state.first_taken is False
        assert state.exit_reason == "FIRST_TARGET"
        assert state.stop == state.entry_price

        reversal = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=2),
            signal.target_levels[0], signal.target_levels[0],
            signal.entry_trigger - D("0.01"), signal.entry_trigger,
            D("100"),
        )
        service.observe_market_bar(
            "XYZ", reversal, reversal.timestamp + timedelta(minutes=1),
        )
        assert submissions[-1] == ("STOP", 100, signal.entry_trigger)
    finally:
        writer.close()


def test_quote_protection_reconciliation_preserves_target_fill_evidence(tmp_path):
    store = ForwardCaptureStore(tmp_path / "target-fill.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": D("100")}
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=lambda *_: True,
        paper_exit_submitter=lambda *_: True,
        paper_position_quantity_source=lambda symbol: position[symbol],
    )
    try:
        _, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        state.authoritative_position_seen = True
        state.remaining = 100
        state.managed_quantity = 100
        state.first_quantity = 50
        state.exit_reason = "FIRST_TARGET"
        state.exit_price = signal.target_levels[0]
        position["XYZ"] = D("50")
        service.reconcile_authoritative_protection("XYZ", signal.timestamp)
        assert state.remaining == 100
        next_bar = MinuteBar("XYZ", signal.timestamp + timedelta(minutes=2),
            signal.entry_trigger, signal.entry_trigger, signal.entry_trigger,
            signal.entry_trigger, D("100"))
        service._advance_authoritative_paper(state, next_bar, next_bar.timestamp + timedelta(minutes=1))
        assert state.first_taken
        assert state.remaining == 50
    finally:
        writer.close()


def test_first_authoritative_partial_fill_rebinds_management_to_actual_quantity(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / 'actual-fill-management.sqlite3')
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {'XYZ': D('0')}
    exits = []
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=lambda *_: True,
        paper_exit_submitter=lambda *args: (exits.append(args) or True),
        paper_position_quantity_source=lambda symbol: position[symbol],
    )
    try:
        _, signal = service.observe(point(
            observation=scanner(bid=D('10.20'), ask=D('10.21')),
        ), account=account())
        assert signal is not None
        state = service._paper['XYZ']
        assert state.remaining > 1
        assert state.authoritative_position_seen is False

        position['XYZ'] = D('1')
        service.reconcile_authoritative_protection('XYZ', signal.timestamp)

        assert state.authoritative_position_seen is True
        assert state.remaining == 1
        assert state.managed_quantity == 1
        assert state.first_quantity == 0
        assert state.second_quantity == 0
        assert all(args[1] <= 1 for args in exits)
    finally:
        writer.close()


def test_protective_stop_identity_cannot_be_relabelled_as_target_fill(tmp_path: Path):
    """CHGA-shaped stop partials never advance a stale target stage."""
    store = ForwardCaptureStore(tmp_path / "chga-stop-identity.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": D("322")}
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=lambda *_: True,
        paper_exit_submitter=lambda symbol, quantity, price, reason, lifecycle:
            PaperExitSubmissionDecision(
                PaperExitSubmissionState.WORKING, symbol, lifecycle, reason,
                order_id=f"{reason}-order",
                activation_timestamp=T0,
            ),
        paper_position_quantity_source=lambda symbol: position[symbol],
        paper_exit_fill_source=lambda symbol, lifecycle: ("PROTECTIVE_STOP", "stop-order"),
    )
    try:
        _, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        state.authoritative_position_seen = True
        state.remaining = 322
        state.managed_quantity = 322
        state.first_quantity = 161
        state.exit_reason = "FIRST_TARGET"  # stale durable context
        state.active_exit_role = "PROTECTIVE_STOP"
        position["XYZ"] = D("78")
        bar = MinuteBar(
            "XYZ", signal.timestamp + timedelta(minutes=1),
            signal.entry_trigger, signal.entry_trigger,
            signal.entry_trigger, signal.entry_trigger, D("100"),
        )
        service._advance_authoritative_paper(state, bar, bar.timestamp)
        assert state.first_taken is False
        assert state.remaining == 78
        assert state.exit_reason is None
    finally:
        writer.close()


def test_recovery_without_durable_book_cannot_claim_target(tmp_path: Path):
    store = ForwardCaptureStore(tmp_path / "recovery-bracket.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": D("322")}
    submissions: list[str] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submissions.append(reason)
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.WORKING, symbol, lifecycle, reason,
            order_id=f"{reason}-{len(submissions)}", activation_timestamp=T0,
        )

    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=lambda *_: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position[symbol],
    )
    try:
        _, signal = service.observe(point(), account=account())
        assert signal is not None
        before = len(submissions)
        assert service.reconcile_authoritative_protection("XYZ", T0) is False
        assert submissions.count("FIRST_TARGET") == 0
        assert submissions.count("STOP") >= 1
        assert service._paper["XYZ"].exit_reason is None
        assert service.reconcile_authoritative_protection("XYZ", T0) is False
        assert len(submissions) == before
    finally:
        writer.close()


def test_recovery_without_book_does_not_repair_stale_first_taken(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "recovery-stale-first-taken.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": D("25")}
    submissions: list[str] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submissions.append(reason)
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.WORKING, symbol, lifecycle, reason,
            order_id=f"{reason}-{len(submissions)}", activation_timestamp=T0,
        )

    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=lambda *_: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position[symbol],
    )
    try:
        _, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        state.first_taken = True  # stale mutable recovery state
        state.first_quantity = 0
        state.exit_reason = "FIRST_TARGET"
        before = len(submissions)
        assert service.reconcile_authoritative_protection("XYZ", T0) is False
        assert submissions.count("FIRST_TARGET") == 0
        assert len(submissions) == before
        assert state.first_taken is True
    finally:
        writer.close()


def test_recovery_without_target_geometry_fails_closed(tmp_path: Path):
    store = ForwardCaptureStore(tmp_path / "recovery-no-target-geometry.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    position = {"XYZ": D("25")}
    submissions: list[str] = []

    def submit_exit(symbol, quantity, price, reason, lifecycle):
        submissions.append(reason)
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.WORKING, symbol, lifecycle, reason,
            order_id=f"{reason}-order", activation_timestamp=T0,
        )

    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=lambda *_: True,
        paper_exit_submitter=submit_exit,
        paper_position_quantity_source=lambda symbol: position[symbol],
    )
    try:
        _, signal = service.observe(point(), account=account())
        assert signal is not None
        state = service._paper["XYZ"]
        state.signal = replace(signal, target_levels=())
        assert service.reconcile_authoritative_protection("XYZ", T0) is False
        assert submissions and set(submissions) == {"STOP"}
    finally:
        writer.close()
