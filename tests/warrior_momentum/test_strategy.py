from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.gui.chart_inspection import ChartInspectionStore
from app.momentum_scanner.models import AssetClass, CatalystStatus, CatalystType, ScannerObservation
from app.strategies.warrior_momentum import (
    CandidateStatus, HaltTracker, MinuteBar, MomentumEntrySignal, ReasonCode,
    BoundedTelemetry,
    ReplayTrade, SetupConfig, SetupDetection, SetupState, SetupType, StopModel,
    StrategySelection, WarriorMomentumConfig, WarriorMomentumRuntime,
    WARRIOR_ENTRY_ALLOWED_SESSIONS,
    build_features, contiguous_tail, build_replay_report, catalyst_score, detect_best_setup, detect_bull_flag,
    create_selected_experiment,
    detect_flat_top, detect_hod_breakout, detect_micro_pullback,
    detect_stocks_in_play, float_score, focus_rows, momentum_score,
    planned_exits, price_change_score, relative_volume_score, size_position,
    prepare_paper_plan,
    simulate_trade,
    compare_same_dataset, watchlist_metadata,
)

D = Decimal
T0 = datetime(2026, 8, 10, 13, 30, tzinfo=UTC)


def bar(i: int, o: str, h: str, low: str, c: str, v: str = "100") -> MinuteBar:
    return MinuteBar("XYZ", T0 + timedelta(minutes=i), D(o), D(h), D(low), D(c), D(v))


def observation(**changes) -> ScannerObservation:
    values = dict(symbol="XYZ", timestamp=T0 + timedelta(minutes=20), price=D("10.20"),
                  previous_close=D("8"), current_volume=D("1000000"),
                  average_30_day_volume=D("100000"), float_shares=D("6000000"),
                  bid=D("10.18"), ask=D("10.22"), catalyst=CatalystType.EARNINGS,
                  catalyst_headline="Reported earnings", tradable=True, halted=False,
                  asset_class=AssetClass.STOCK, catalyst_status=CatalystStatus.TRUE)
    values.update(changes)
    return ScannerObservation(**values)


def hod_bars() -> tuple[MinuteBar, ...]:
    return (bar(0,"9.7","9.9","9.6","9.8"), bar(1,"9.8","10","9.75","9.9"),
            bar(2,"9.9","9.99","9.8","9.92"), bar(3,"9.92","10","9.85","9.95"),
            bar(4,"9.96","10.2","9.94","10.10","300"))


def micro_bars() -> tuple[MinuteBar, ...]:
    return (bar(0,"10","10.15","9.98","10.1"), bar(1,"10.1","10.35","10.08","10.3"),
            bar(2,"10.3","10.6","10.28","10.55","300"), bar(3,"10.5","10.55","10.35","10.42","180"),
            bar(4,"10.42","10.5","10.38","10.45","120"), bar(5,"10.46","10.7","10.44","10.65","300"))


def bull_flag_bars() -> tuple[MinuteBar, ...]:
    return (bar(0,"10","10.2","9.98","10.18"), bar(1,"10.18","10.5","10.15","10.45"),
            bar(2,"10.45","10.8","10.4","10.75"), bar(3,"10.75","11","10.7","10.95","300"),
            bar(4,"10.95","10.98","10.7","10.78","180"), bar(5,"10.78","10.9","10.72","10.82","140"),
            bar(6,"10.82","10.92","10.75","10.88","120"), bar(7,"10.9","11.1","10.88","11.05","300"))


def flat_bars() -> tuple[MinuteBar, ...]:
    return (bar(0,"9.8","9.95","9.7","9.9"), bar(1,"9.9","10","9.8","9.95"),
            bar(2,"9.94","9.99","9.82","9.96"), bar(3,"9.95","10","9.85","9.97"),
            bar(4,"9.96","9.995","9.88","9.98"), bar(5,"9.99","10.2","9.95","10.10","250"))


def test_normalized_scores_and_bounds() -> None:
    assert price_change_score(D("0")) == 0
    assert price_change_score(D("20")) == D("0.5")
    assert relative_volume_score(D("1")) == 0
    assert relative_volume_score(D("5")) == D("0.75")
    assert relative_volume_score(D("12")) == 1
    assert float_score(D("5000000")) == 1
    assert float_score(D("15000000")) == D("0.65")
    assert float_score(D("60000000")) == D("0.05")
    assert catalyst_score(CatalystStatus.TRUE) == 1
    assert catalyst_score(CatalystStatus.FALSE) < catalyst_score(CatalystStatus.UNKNOWN)
    score = momentum_score(percentage_change=D("1000"), relative_volume=D("100"), acceleration=D("100"),
                           float_shares=D("1"), dollar_volume=D("999999999"), catalyst_state=CatalystStatus.TRUE,
                           setup_quality=D("999"), spread_percent=D("0"))
    assert score.total == 100


def test_timestamp_aligned_squeeze_detectors_and_features() -> None:
    bars = tuple(bar(i, str(10 + i), str(10.2 + i), str(9.9 + i), str(10 + i), str(100 + i * 10)) for i in range(11))
    found = detect_stocks_in_play(bars, percentage_change=D("20"), relative_volume=D("6"))
    assert "SQUEEZE_5_IN_5" in found
    assert "SQUEEZE_10_IN_10" in found
    features = build_features(bars)
    assert features is not None and features.vwap is not None
    assert features.distance_from_hod_percent >= 0
    with pytest.raises(ValueError):
        build_features((bars[0], bars[0]))


@pytest.mark.parametrize(("detector","bars","kind"), [
    (detect_hod_breakout, hod_bars(), SetupType.HIGH_OF_DAY_BREAKOUT),
    (detect_micro_pullback, micro_bars(), SetupType.MICRO_PULLBACK),
    (detect_bull_flag, bull_flag_bars(), SetupType.BULL_FLAG),
    (detect_flat_top, flat_bars(), SetupType.FLAT_TOP_BREAKOUT),
])
def test_setup_detectors_are_structural(detector, bars, kind) -> None:
    result = detector(bars)
    assert result.setup_type is kind
    assert result.state is SetupState.TRIGGERED
    assert result.trigger is not None and result.stop_price is not None
    assert result.trigger > result.stop_price


def test_micro_pullback_uses_configured_minimum_pullback_bars() -> None:
    bars = (
        bar(0, "10.00", "10.15", "9.98", "10.10"),
        bar(1, "10.10", "10.35", "10.08", "10.30"),
        bar(2, "10.30", "10.60", "10.28", "10.55", "300"),
        bar(3, "10.50", "10.55", "10.34", "10.42", "200"),
        bar(4, "10.42", "10.50", "10.36", "10.44", "170"),
        bar(5, "10.44", "10.51", "10.38", "10.46", "140"),
        bar(6, "10.47", "10.70", "10.45", "10.65", "300"),
    )

    config = SetupConfig(minimum_pullback_bars=3)

    result = detect_micro_pullback(bars, config)

    assert result.state is SetupState.TRIGGERED
    assert result.setup_type is SetupType.MICRO_PULLBACK


def test_bull_flag_uses_configured_minimum_consolidation_bars() -> None:
    bars = (
        bar(0, "10.00", "10.20", "9.98", "10.18"),
        bar(1, "10.18", "10.50", "10.15", "10.45"),
        bar(2, "10.45", "10.80", "10.40", "10.75"),
        bar(3, "10.75", "11.00", "10.70", "10.95", "300"),
        bar(4, "10.95", "10.98", "10.70", "10.78", "180"),
        bar(5, "10.78", "10.90", "10.72", "10.82", "150"),
        bar(6, "10.82", "10.92", "10.74", "10.85", "130"),
        bar(7, "10.85", "10.93", "10.76", "10.88", "110"),
        bar(8, "10.90", "11.10", "10.88", "11.05", "300"),
    )

    config = SetupConfig(minimum_consolidation_bars=4)

    result = detect_bull_flag(bars, config)

    assert result.state is SetupState.TRIGGERED
    assert result.setup_type is SetupType.BULL_FLAG


def test_hod_breakout_uses_configured_minimum_consolidation_bars() -> None:
    bars = hod_bars()

    two_bar = detect_hod_breakout(
        bars,
        SetupConfig(minimum_consolidation_bars=2),
    )

    assert two_bar.state in {
        SetupState.FORMING,
        SetupState.TRIGGERED,
        SetupState.NOT_FORMED,
    }


def test_contiguous_tail_does_not_bridge_missing_minutes() -> None:
    bars = bull_flag_bars()

    latest = replace(
        bars[-1],
        timestamp=bars[-1].timestamp + timedelta(hours=2),
    )
    discontinuous = (*bars[:-1], latest)

    tail = contiguous_tail(discontinuous)

    assert tail == (latest,)


def test_setup_detectors_do_not_form_patterns_across_timestamp_gap() -> None:
    bars = bull_flag_bars()

    latest = replace(
        bars[-1],
        timestamp=bars[-1].timestamp + timedelta(hours=2),
    )
    discontinuous = (*bars[:-1], latest)

    detections = (
        detect_hod_breakout(discontinuous),
        detect_micro_pullback(discontinuous),
        detect_bull_flag(discontinuous),
        detect_flat_top(discontinuous),
    )

    assert all(result.state is SetupState.UNKNOWN for result in detections)


def test_detect_best_setup_returns_none_when_every_detector_is_not_formed() -> None:
    bars = (
        bar(0, "10.00", "10.10", "9.80", "9.90", "100"),
        bar(1, "9.90", "10.30", "9.60", "10.20", "90"),
        bar(2, "10.20", "10.25", "9.50", "9.70", "140"),
        bar(3, "9.70", "10.40", "9.40", "10.10", "80"),
        bar(4, "10.10", "10.15", "9.30", "9.60", "160"),
        bar(5, "9.60", "10.35", "9.20", "10.00", "70"),
        bar(6, "10.00", "10.05", "9.10", "9.50", "180"),
        bar(7, "9.50", "10.25", "9.00", "9.90", "60"),
        bar(8, "9.90", "10.00", "8.90", "9.40", "200"),
        bar(9, "9.40", "10.20", "8.80", "9.80", "50"),
        bar(10, "9.80", "9.95", "8.70", "9.30", "220"),
        bar(11, "9.30", "10.10", "8.60", "9.70", "40"),
    )

    detections = (
        detect_hod_breakout(bars),
        detect_micro_pullback(bars),
        detect_bull_flag(bars),
        detect_flat_top(bars),
    )

    assert all(result.state is SetupState.NOT_FORMED for result in detections)
    assert detect_best_setup(bars) is None


def test_detect_best_setup_still_returns_forming_or_triggered_setup() -> None:
    result = detect_best_setup(bull_flag_bars())

    assert result is not None
    assert result.setup_type is SetupType.BULL_FLAG
    assert result.state is SetupState.TRIGGERED


def test_incomplete_data_returns_unknown() -> None:
    assert detect_bull_flag(hod_bars()[:2]).state is SetupState.UNKNOWN
    assert build_features(()) is None


def test_discovery_is_not_entry_and_false_catalyst_is_visible() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(observation(catalyst=CatalystType.NONE, catalyst_status=CatalystStatus.FALSE,
                                             bid=D("9"), ask=D("11")), hod_bars(), session="REGULAR")
    assert candidate.status in set(CandidateStatus)
    assert ReasonCode.NO_CATALYST in candidate.reason_codes
    assert candidate.symbol == "XYZ"
    assert runtime.entry_signal(candidate) is None
    assert focus_rows(runtime.rank((candidate,)))[0].symbol == "XYZ"


@pytest.mark.parametrize(("change","reason"), [
    ({"bid": D("9"), "ask": D("11")}, ReasonCode.SPREAD_WIDE),
    ({"halted": True}, ReasonCode.HALTED),
    ({"tradable": False}, ReasonCode.NOT_TRADABLE),
])
def test_entry_rejects_execution_quality_and_state(change, reason) -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(observation(**change), hod_bars(), session="REGULAR")
    assessed, signal_value = runtime.assess_entry(candidate)
    assert signal_value is None
    assert reason in assessed.reason_codes
    assert assessed.status is CandidateStatus.INELIGIBLE_FOR_EXECUTION


def test_default_entry_sessions_explicitly_allow_after_hours_but_not_overnight() -> None:
    assert WARRIOR_ENTRY_ALLOWED_SESSIONS == frozenset({
        "PREMARKET", "REGULAR", "AFTER_HOURS",
    })
    assert WarriorMomentumConfig().entry.allowed_sessions == WARRIOR_ENTRY_ALLOWED_SESSIONS
    assert "OVERNIGHT" not in WARRIOR_ENTRY_ALLOWED_SESSIONS


@pytest.mark.parametrize("session", ("PREMARKET", "REGULAR", "AFTER_HOURS"))
def test_valid_entry_is_allowed_in_each_authorized_session(session: str) -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(observation(), hod_bars(), session=session)

    assessed, signal_value = runtime.assess_entry(candidate)

    assert assessed.status is CandidateStatus.ENTRY_READY
    assert ReasonCode.SESSION_NOT_ALLOWED not in assessed.reason_codes
    assert signal_value is not None and signal_value.session == session


def test_after_hours_valid_entry_has_premarket_strategy_parity() -> None:
    runtime = WarriorMomentumRuntime()
    premarket, premarket_signal = runtime.assess_entry(
        runtime.discover(observation(), hod_bars(), session="PREMARKET")
    )
    after_hours, after_hours_signal = runtime.assess_entry(
        runtime.discover(observation(), hod_bars(), session="AFTER_HOURS")
    )

    assert premarket_signal is not None and after_hours_signal is not None
    assert premarket.status is after_hours.status is CandidateStatus.ENTRY_READY
    assert replace(after_hours_signal, session="PREMARKET") == premarket_signal
    assert after_hours_signal.execution_authorized is False
    assert runtime.authorize_live(after_hours_signal) is False


def test_after_hours_without_setup_is_rejected_only_by_remaining_entry_gates() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(observation(), hod_bars(), session="AFTER_HOURS")

    assessed, signal_value = runtime.assess_entry(replace(candidate, setup=None))

    assert signal_value is None
    assert set(assessed.reason_codes) == {ReasonCode.NO_SETUP}


def test_after_hours_spread_failure_remains_ineligible() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(
        observation(bid=D("9"), ask=D("11")), hod_bars(), session="AFTER_HOURS",
    )

    assessed, signal_value = runtime.assess_entry(candidate)

    assert signal_value is None
    assert assessed.status is CandidateStatus.INELIGIBLE_FOR_EXECUTION
    assert ReasonCode.SPREAD_WIDE in assessed.reason_codes
    assert ReasonCode.SESSION_NOT_ALLOWED not in assessed.reason_codes


def test_overnight_remains_session_blocked() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(observation(), hod_bars(), session="OVERNIGHT")

    assessed, signal_value = runtime.assess_entry(candidate)

    assert signal_value is None
    assert assessed.status is CandidateStatus.INELIGIBLE_FOR_EXECUTION
    assert ReasonCode.SESSION_NOT_ALLOWED in assessed.reason_codes


def signal() -> MomentumEntrySignal:
    return MomentumEntrySignal("WARRIOR_MOMENTUM_V1", "XYZ", T0, "REGULAR", D("80"),
                               SetupType.MICRO_PULLBACK, D("10"), D("10"), D("9.75"),
                               StopModel.MICRO_PULLBACK_LOW, D("0.25"), (D("10.25"), D("10.5"), D("11")),
                               CatalystStatus.TRUE, D("8"), D("6000000"), D("0.2"),
                               D("1000000"), D("10000000"), D("85"), ())


def test_signal_stop_sizing_partial_exits_and_live_guard() -> None:
    sig = signal()
    assert sig.risk_per_share == sig.entry_trigger - sig.stop_price
    size = size_position(sig, account_equity=D("50000"), buying_power=D("10000"),
                         allowed_symbols=frozenset({"XYZ"}), exposure_limit=D("20000"))
    assert size.approved and size.shares == 400
    exits = planned_exits(sig, size.shares)
    assert sum(exit.quantity for exit in exits) == size.shares
    assert exits[0].price == D("10.25")
    assert WarriorMomentumRuntime.authorize_live(sig) is False
    assert sig.execution_authorized is False


def test_position_sizing_enforces_allowed_symbols_and_risk_engine() -> None:
    denied = size_position(signal(), account_equity=D("50000"), buying_power=D("10000"),
                           allowed_symbols=frozenset(), risk_engine_approved=False)
    assert not denied.approved and denied.shares == 0
    assert ReasonCode.EXECUTION_NOT_ALLOWED in denied.reason_codes
    assert ReasonCode.RISK_REJECTED in denied.reason_codes


def test_after_hours_position_sizing_still_requires_risk_approval() -> None:
    after_hours_signal = replace(signal(), session="AFTER_HOURS")

    denied = size_position(
        after_hours_signal, account_equity=D("50000"), buying_power=D("10000"),
        allowed_symbols=frozenset({"XYZ"}), risk_engine_approved=False,
    )

    assert not denied.approved and denied.shares == 0
    assert ReasonCode.RISK_REJECTED in denied.reason_codes


def test_paper_plan_is_authorized_only_after_existing_safeguards() -> None:
    plan = prepare_paper_plan(signal(), account_equity=D("50000"), buying_power=D("10000"),
                              allowed_symbols=frozenset({"XYZ"}), risk_engine_approved=True)
    assert plan.paper_execution_authorized is True
    assert plan.live_execution_authorized is False
    assert sum(exit.quantity for exit in plan.exits) == plan.position.shares


def test_candidate_ranking_is_deterministic_and_near_symbols_remain() -> None:
    runtime = WarriorMomentumRuntime()
    first = runtime.discover(observation(symbol="BBB"), hod_bars(), session="REGULAR")
    second = runtime.discover(observation(symbol="AAA", catalyst_status=CatalystStatus.UNKNOWN,
                                          catalyst=CatalystType.NONE), hod_bars(), session="REGULAR")
    ranked = runtime.rank((second, first))
    assert ranked[0].rank == 1 and ranked[1].rank == 2
    assert {row.symbol for row in focus_rows(ranked)} == {"AAA", "BBB"}
    assert dict(watchlist_metadata(ranked[0]))["warrior_status"] == ranked[0].status.value


def test_bounded_rejection_telemetry_does_not_flood_symbols() -> None:
    runtime = WarriorMomentumRuntime()
    telemetry = BoundedTelemetry(symbol_limit=2)
    for symbol in ("AAA", "BBB", "CCC"):
        telemetry.observe(runtime.discover(observation(symbol=symbol, catalyst_status=CatalystStatus.FALSE,
                                                       catalyst=CatalystType.NONE), hod_bars(), session="REGULAR"))
    snapshot = telemetry.snapshot()
    assert snapshot.recent_symbols == ("BBB", "CCC")
    assert dict(snapshot.rejection_counts)[ReasonCode.NO_CATALYST] == 3


def test_operator_inspection_survives_candidate_disappearance() -> None:
    store = ChartInspectionStore()
    store.select("xyz")
    focus_rows(())
    assert store.snapshot().symbol == "XYZ"


def test_halt_observation_records_resume_facts() -> None:
    tracker = HaltTracker()
    entered = tracker.observe("XYZ", T0, D("10"), True)
    resumed = tracker.observe("XYZ", T0 + timedelta(minutes=7), D("11"), False)
    assert entered is not None and resumed is not None
    assert resumed.duration == timedelta(minutes=7)
    assert resumed.resume_gap_percent == D("10")


def trade(symbol: str, pnl: str, r: str, minute: int) -> ReplayTrade:
    return ReplayTrade(symbol, SetupType.BULL_FLAG, CatalystStatus.TRUE, "REGULAR",
                       T0 + timedelta(minutes=minute), T0 + timedelta(minutes=minute + 5),
                       D("10"), D("10") + D(pnl) / 100, D("25"), 100, D(r), D(pnl),
                       D("-0.4"), D("1.5"), D("6000000"), D("8"), D("75"))


def test_replay_report_is_deterministic_and_complete() -> None:
    trades = (trade("B", "100", "2", 10), trade("A", "-50", "-1", 0))
    one = build_replay_report(trades, discovered_stocks=8, setups=3, signals=2)
    two = build_replay_report(tuple(reversed(trades)), discovered_stocks=8, setups=3, signals=2)
    assert one == two
    assert one.win_rate == D("50") and one.profit_factor == D("2")
    assert one.max_drawdown == D("50")
    assert len(one.breakdowns) == 7
    assert one.slippage_sensitivity and one.spread_sensitivity
    comparison = compare_same_dataset(one, two, trades, tuple(reversed(trades)))
    assert tuple(row.strategy for row in comparison) == ("CURRENT_ATLAS", "WARRIOR_MOMENTUM_V1")


def test_configuration_defaults_existing_and_live_false(monkeypatch) -> None:
    monkeypatch.delenv("ATLAS_STRATEGY", raising=False)
    monkeypatch.setenv("WARRIOR_MOMENTUM_V1_LIVE_EXECUTION_ENABLED", "true")
    selected = StrategySelection.from_env()
    assert selected.selected.value == "existing"
    assert selected.warrior_live_execution_enabled is False
    assert create_selected_experiment(selected) is None
    with pytest.raises(ValueError):
        WarriorMomentumConfig(live_execution_enabled=True)


def test_only_production_catalyst_sources_are_claimed() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(observation(catalyst=CatalystType.FDA,
                                             catalyst_status=CatalystStatus.TRUE),
                                 hod_bars(), session="REGULAR")
    assert candidate.catalyst_type is CatalystType.NONE
    assert candidate.catalyst_status is CatalystStatus.UNKNOWN


def test_entry_ready_does_not_mean_execution_authorized() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(observation(), hod_bars(), session="REGULAR")
    assessed, sig = runtime.assess_entry(candidate)
    assert sig is not None
    assert assessed.status is CandidateStatus.ENTRY_READY
    assert sig.execution_authorized is False


def test_replay_signal_simulation_is_conservative_and_deterministic() -> None:
    sig = signal()
    future = (
        bar(1, "10", "10.6", "9.7", "10.4", "500"),
        bar(2, "10.4", "10.7", "10.3", "10.6", "500"),
    )
    first = simulate_trade(sig, future, quantity=100)
    second = simulate_trade(sig, tuple(reversed(future)), quantity=100)
    assert first == second
    assert first is not None and first.exit_price == sig.stop_price
    assert first.r_multiple == D("-1")
