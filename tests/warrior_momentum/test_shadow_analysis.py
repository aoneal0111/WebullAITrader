from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from inspect import signature
from pathlib import Path

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)
from app.strategies.warrior_momentum import (
    CandidateStatus, CaptureRecord, CaptureRecordType, FloatProvenance,
    ForwardCaptureConfiguration, ForwardCaptureStore, ForwardCaptureWriter,
    MinuteBar, MomentumCandidate, MomentumScore, PointInTimeObservation,
    ReasonCode, SetupDetection, SetupState, SetupType, StopModel,
    WarriorForwardCaptureService, build_rejection_attribution,
)
from app.strategies.warrior_momentum.shadow_analysis import ShadowOpportunityAnalyzer


T0 = datetime(2026, 8, 27, 15, 0, tzinfo=UTC)


def candidate(
    *, reasons=(ReasonCode.SPREAD_WIDE,), setup: SetupDetection | None = None,
    timestamp: datetime = T0,
) -> MomentumCandidate:
    return MomentumCandidate(
        4, "XYZ", timestamp, D("10"), D("25"), D("8"), D("6000000"),
        D("1000000"), D("10000000"), D("1.5"), CatalystStatus.TRUE,
        CatalystType.EARNINGS, MomentumScore(D("80"), (("quality", D("80")),)),
        (), setup, "REGULAR", CandidateStatus.INELIGIBLE_FOR_EXECUTION,
        True, False, D("0.5"), reasons, (),
    )


def point(*, timestamp: datetime = T0) -> PointInTimeObservation:
    observation = ScannerObservation(
        "XYZ", timestamp, D("10"), D("8"), D("1000000"), D("125000"),
        D("6000000"), D("9.90"), D("10.10"), CatalystType.EARNINGS,
        "synthetic", True, False, AssetClass.STOCK, CatalystStatus.TRUE,
    )
    return PointInTimeObservation(
        observation, "REGULAR", (), FloatProvenance.AUTHORITATIVE_FLOAT,
        scanner_rank=3, scanner_score=92, scanner_classification="QUALIFYING",
        scanner_failed_rules=("spread",),
    )


def bar(minute: int, opened: str, high: str, low: str, close: str) -> MinuteBar:
    return MinuteBar(
        "XYZ", T0 + timedelta(minutes=minute), D(opened), D(high), D(low),
        D(close), D("1000"),
    )


def register(
    store: ForwardCaptureStore, analyzer: ShadowOpportunityAnalyzer,
    value: PointInTimeObservation, item: MomentumCandidate,
) -> CaptureRecord:
    decision = CaptureRecord.create(
        CaptureRecordType.DECISION, "XYZ", item.timestamp,
        {"status": item.status.value, "reason_codes": tuple(code.value for code in item.reason_codes)},
    )
    evaluation = analyzer.observe_rejection(
        decision, value, item, tuple(code.value for code in item.reason_codes),
        scanner_rank=value.scanner_rank, scanner_score=value.scanner_score,
        scanner_classification=value.scanner_classification,
        scanner_failed_rules=value.scanner_failed_rules,
    )
    store.append_batch((decision, evaluation))
    return evaluation


def persist_bars_and_outcomes(
    store: ForwardCaptureStore, analyzer: ShadowOpportunityAnalyzer,
    values: tuple[MinuteBar, ...],
) -> tuple[CaptureRecord, ...]:
    outcomes: list[CaptureRecord] = []
    for value in values:
        stored_bar = CaptureRecord.create(
            CaptureRecordType.MINUTE_BAR, value.symbol, value.timestamp + timedelta(minutes=1),
            {"bar_timestamp": value.timestamp, "open": value.open, "high": value.high,
             "low": value.low, "close": value.close, "volume": value.volume},
            identity_parts=(value.timestamp.isoformat(),),
        )
        generated = analyzer.observe_bar(value)
        store.append_batch((stored_bar, *generated))
        outcomes.extend(generated)
    return tuple(outcomes)


def test_rejected_candidate_is_followed_at_1_2_5_10_minutes_with_excursions(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "windows.sqlite3")
    analyzer = ShadowOpportunityAnalyzer(store)
    evaluation = register(store, analyzer, point(), candidate(reasons=(
        ReasonCode.SPREAD_WIDE, ReasonCode.NO_SETUP,
    )))
    values = tuple(
        bar(index, str(10 + index / 100), str(10.1 + index / 100),
            str(9.9 - index / 200), str(10 + index / 100))
        for index in range(10)
    )
    outcomes = persist_bars_and_outcomes(store, analyzer, values)

    assert [item.payload["horizon_minutes"] for item in outcomes] == [1, 2, 5, 10]
    assert all(item.payload["status"] == "COMPLETE" for item in outcomes)
    ten = outcomes[-1].payload
    assert D(ten["subsequent_price"]) > D(ten["evaluation_price"])
    assert D(ten["mfe_percent"]) > 0 and D(ten["mae_percent"]) < 0
    assert ten["hypothetical_trade"]["applicable"] is False
    assert ten["hypothetical_trade"]["trigger"] is None
    assert ten["hypothetical_trade"]["stop"] is None
    assert tuple(ten["reason_codes"]) == ("SPREAD_WIDE", "NO_SETUP")
    assert ten["evaluation_record_id"] == evaluation.record_id
    captured = evaluation.payload
    assert (captured["scanner_rank"], captured["scanner_score"]) == (3, 92)
    assert captured["scanner_classification"] == "QUALIFYING"
    assert captured["scanner_failed_rules"] == ["spread"]
    report = build_rejection_attribution(store)
    assert report["SPREAD_WIDE"]["candidates_rejected"] == 1
    assert report["SPREAD_WIDE + NO_SETUP"]["candidates_with_complete_forward_data"] == 1


def test_falling_candidate_is_a_good_rejection(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "fall.sqlite3")
    analyzer = ShadowOpportunityAnalyzer(store)
    register(store, analyzer, point(), candidate())
    outcomes = persist_bars_and_outcomes(store, analyzer, tuple(
        bar(index, "10", "10.01", str(10 - index / 20), str(10 - index / 25))
        for index in range(10)
    ))
    assert outcomes[-1].payload["return_percent"].startswith("-")
    assert outcomes[-1].payload["classification"] == "GOOD_REJECTION"


def test_valid_plan_records_favorable_move_before_later_stop(tmp_path: Path) -> None:
    setup = SetupDetection(
        SetupType.BULL_FLAG, SetupState.TRIGGERED, D("90"), D("10.20"),
        D("10.00"), StopModel.FLAG_LOW,
    )
    store = ForwardCaptureStore(tmp_path / "reward-first.sqlite3")
    analyzer = ShadowOpportunityAnalyzer(store)
    register(store, analyzer, point(), candidate(setup=setup))
    outcomes = persist_bars_and_outcomes(store, analyzer, (
        bar(0, "10.1", "10.25", "10.10", "10.22"),
        bar(1, "10.22", "10.45", "10.15", "10.40"),
        bar(2, "10.4", "10.42", "9.95", "10.00"),
        *(bar(i, "10", "10.1", "9.98", "10") for i in range(3, 10)),
    ))
    plan = outcomes[-1].payload["hypothetical_trade"]
    assert plan["state"] == "HIT_STOP"
    assert plan["reward_hits"][0]["multiple"] == "1"
    assert plan["reward_hits"][0]["bar_timestamp"] < plan["stop_hit_at_bar"]
    assert outcomes[-1].payload["classification"] == "MISSED_OPPORTUNITY"


def test_stop_before_later_target_is_dangerous_missed_opportunity(tmp_path: Path) -> None:
    setup = SetupDetection(
        SetupType.BULL_FLAG, SetupState.TRIGGERED, D("90"), D("10.20"),
        D("10.00"), StopModel.FLAG_LOW,
    )
    store = ForwardCaptureStore(tmp_path / "stop-first.sqlite3")
    analyzer = ShadowOpportunityAnalyzer(store)
    register(store, analyzer, point(), candidate(setup=setup))
    outcomes = persist_bars_and_outcomes(store, analyzer, (
        bar(0, "10.1", "10.25", "10.10", "10.22"),
        bar(1, "10.2", "10.25", "9.95", "10.00"),
        bar(2, "10", "10.50", "9.98", "10.45"),
        *(bar(i, "10.4", "10.5", "10.2", "10.4") for i in range(3, 10)),
    ))
    plan = outcomes[-1].payload["hypothetical_trade"]
    assert plan["stop_hit_at_bar"] < outcomes[-1].payload["mfe_source_bar_timestamp"]
    assert plan["reward_hits"] == []
    assert outcomes[-1].payload["classification"] == "DANGEROUS_MISSED_OPPORTUNITY"


def test_missing_future_data_and_session_boundary_are_explicit(tmp_path: Path) -> None:
    missing_store = ForwardCaptureStore(tmp_path / "missing.sqlite3")
    missing = ShadowOpportunityAnalyzer(missing_store)
    register(missing_store, missing, point(), candidate())
    unavailable = missing.finalize_due(T0 + timedelta(minutes=11))
    assert len(unavailable) == 4
    assert {item.payload["status"] for item in unavailable} == {
        "INCOMPLETE_MISSING_FUTURE_DATA"
    }

    boundary_time = datetime(2026, 8, 27, 19, 59, tzinfo=UTC)
    boundary_store = ForwardCaptureStore(tmp_path / "boundary.sqlite3")
    boundary = ShadowOpportunityAnalyzer(boundary_store)
    boundary_candidate = candidate(timestamp=boundary_time)
    register(boundary_store, boundary, point(timestamp=boundary_time), boundary_candidate)
    crossed = boundary.finalize_due(boundary_time + timedelta(minutes=2))
    one = next(item for item in crossed if item.payload["horizon_minutes"] == 1)
    assert one.payload["status"] == "INCOMPLETE_SESSION_BOUNDARY"


def test_restart_idempotency_and_duplicate_evaluation_protection(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "restart.sqlite3")
    analyzer = ShadowOpportunityAnalyzer(store)
    first = register(store, analyzer, point(), candidate())
    duplicate = analyzer.observe_rejection(
        next(item for item in store.records(record_type=CaptureRecordType.DECISION)),
        point(), candidate(), ("SPREAD_WIDE",),
        scanner_rank=3, scanner_score=92, scanner_classification="QUALIFYING",
        scanner_failed_rules=("spread",),
    )
    assert duplicate.record_id == first.record_id
    assert store.append_batch((duplicate,)) == (0, 1)
    persist_bars_and_outcomes(store, analyzer, tuple(
        bar(index, "10", "10.1", "9.9", "10") for index in range(10)
    ))
    assert len(store.records(record_type=CaptureRecordType.SHADOW_OUTCOME)) == 4
    restarted = ShadowOpportunityAnalyzer(store)
    assert restarted.observe_bar(bar(9, "10", "10.1", "9.9", "10")) == ()
    assert restarted.finalize_due(T0 + timedelta(minutes=20)) == ()


def _integration_point() -> PointInTimeObservation:
    observation = ScannerObservation(
        "XYZ", T0, D("10"), D("8"), D("1000000"), D("100000"),
        D("6000000"), D("9.99"), D("10.01"), CatalystType.EARNINGS,
        "synthetic", True, False, AssetClass.STOCK, CatalystStatus.TRUE,
    )
    return PointInTimeObservation(observation, "REGULAR", ())


def test_enabled_and_disabled_shadow_leave_production_decision_unchanged(tmp_path: Path) -> None:
    results = []
    record_sets = []
    for enabled in (True, False):
        store = ForwardCaptureStore(tmp_path / f"enabled-{enabled}.sqlite3")
        writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
        service = WarriorForwardCaptureService(
            store, writer,
            capture_config=ForwardCaptureConfiguration(
                storage_path=store.path, shadow_analysis_enabled=enabled,
            ),
        )
        results.append(service.observe(_integration_point()))
        writer.close()
        record_sets.append(tuple(
            (item.record_type, item.symbol, item.timestamp, item.payload_json)
            for item in store.records()
            if item.record_type not in {
                CaptureRecordType.SHADOW_EVALUATION, CaptureRecordType.SHADOW_OUTCOME,
            }
        ))
        assert bool(store.records(record_type=CaptureRecordType.SHADOW_EVALUATION)) is enabled
    assert results[0] == results[1]
    assert record_sets[0] == record_sets[1]


def test_shadow_has_no_paper_or_live_execution_surface() -> None:
    parameters = tuple(signature(ShadowOpportunityAnalyzer).parameters)
    assert parameters == ("store", "config")
    forbidden = {
        "submit_entry", "submit_exit", "place_order", "authorize_live",
        "trading_service", "order_gateway", "paper_order_gateway",
    }
    assert forbidden.isdisjoint(name.lower() for name in dir(ShadowOpportunityAnalyzer))
    source = Path("app/strategies/warrior_momentum/shadow_analysis.py").read_text("utf-8")
    assert "from app.services.trading_service" not in source
    assert "AutonomousPaperExecutionBridge" not in source
    assert "PaperOrderGateway" not in source
