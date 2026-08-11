from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)
from app.strategies.warrior_momentum import (
    ExecutionScenario, ExecutionScenarioName, MinuteBar, MomentumEntrySignal,
    PaperExit, ReplayLedgerEntry, SetupType, StopModel, WarriorMomentumRuntime,
    completed_bars_as_of, grouped_results, load_dataset, performance_metrics,
    simulate_managed_trade,
)

T0 = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)


def bar(i: int, *, open: str = "10", high: str = "10.2", low: str = "9.8",
        close: str = "10.1", volume: str = "100") -> MinuteBar:
    return MinuteBar("XYZ", T0 + timedelta(minutes=i), D(open), D(high), D(low), D(close), D(volume))


def observation(timestamp: datetime) -> ScannerObservation:
    return ScannerObservation(
        "XYZ", timestamp, D("10.1"), D("9"), D("1000000"), D("100000"),
        None, None, None, CatalystType.NONE, None, True, False,
        AssetClass.STOCK, CatalystStatus.UNAVAILABLE,
    )


def signal() -> MomentumEntrySignal:
    return MomentumEntrySignal(
        "WARRIOR_MOMENTUM_V1", "XYZ", T0, "REGULAR", D("75"),
        SetupType.BULL_FLAG, D("10"), D("10"), D("9"), StopModel.FLAG_LOW,
        D("1"), (D("11"), D("12"), D("13")), CatalystStatus.TRUE,
        D("8"), None, D("0.25"), D("1000000"), D("10000000"), D("90"), (),
    )


def scenario(*, slip: str = "0", spread: str = "0", delay: int = 0,
             fill: str = "1") -> ExecutionScenario:
    return ExecutionScenario(ExecutionScenarioName.BASELINE, D(slip), D(spread), D(fill), delay)


def test_completed_bar_boundary_excludes_incomplete_and_future_bars() -> None:
    bars = (bar(0), bar(1), bar(2))
    assert completed_bars_as_of(bars, T0 + timedelta(minutes=1)) == (bars[0],)
    assert completed_bars_as_of(bars, T0 + timedelta(minutes=2)) == bars[:2]


def test_runtime_result_cannot_be_changed_by_future_bar() -> None:
    runtime = WarriorMomentumRuntime()
    past = (bar(0), bar(1))
    future = bar(3, open="10", high="30", low="9", close="29", volume="999999")
    at = T0 + timedelta(minutes=3)
    without_future = runtime.discover(observation(at), past, session="REGULAR")
    with_future = runtime.discover(observation(at), (*past, future), session="REGULAR")
    assert with_future == without_future


def test_same_bar_stop_target_ambiguity_is_stop_first() -> None:
    result = simulate_managed_trade(
        signal(), (bar(0, high="13.5", low="8.5", close="12"),),
        requested_quantity=100, scenario=scenario(),
    )
    assert result is not None
    assert result.exits == (PaperExit("STOP", D("9"), 100),)
    assert result.realized_r == D("-1")


def test_partial_exits_realize_one_point_seven_five_r() -> None:
    bars = (
        bar(0, high="11.1", low="9.5", close="10.8"),
        bar(1, open="10.8", high="12.1", low="10.1", close="11.8"),
        bar(2, open="11.8", high="13.1", low="11", close="13"),
    )
    result = simulate_managed_trade(signal(), bars, requested_quantity=100, scenario=scenario())
    assert result is not None
    assert tuple(exit.quantity for exit in result.exits) == (50, 25, 25)
    assert result.realized_r == D("1.75")


def test_slippage_and_spread_reduce_realized_r() -> None:
    bars = (bar(0, high="13.5", low="9.5", close="13"),)
    ideal = simulate_managed_trade(signal(), bars, requested_quantity=100, scenario=scenario())
    slipped = simulate_managed_trade(signal(), bars, requested_quantity=100, scenario=scenario(slip="0.05"))
    spread = simulate_managed_trade(signal(), bars, requested_quantity=100, scenario=scenario(spread="1"))
    assert ideal is not None and slipped is not None and spread is not None
    assert ideal.realized_r > slipped.realized_r
    assert ideal.realized_r > spread.realized_r


def test_entry_delay_changes_only_future_fill_path() -> None:
    bars = (
        bar(0, high="13.5", low="9.5", close="13"),
        bar(1, high="10.2", low="8.5", close="9"),
    )
    immediate = simulate_managed_trade(signal(), bars, requested_quantity=100, scenario=scenario(delay=0))
    delayed = simulate_managed_trade(signal(), bars, requested_quantity=100, scenario=scenario(delay=1))
    assert immediate is not None and delayed is not None
    assert immediate.realized_r > 0
    assert delayed.realized_r < 0


def ledger(i: int, r: str, *, setup: SetupType = SetupType.BULL_FLAG) -> ReplayLedgerEntry:
    start = T0 + timedelta(minutes=i)
    return ReplayLedgerEntry(
        ExecutionScenarioName.BASELINE, start, start + timedelta(minutes=5), "XYZ",
        setup, StopModel.FLAG_LOW, D("70"), D("10"), D("9"), D("1"), 100,
        (D("11"), D("12"), D("13")), (PaperExit("EXIT", D("10") + D(r), 100),),
        D(r), D("-0.5"), D("1.5"), D("300"), "TRUE", D("8"), "UNKNOWN",
        "$5-$10", "REGULAR", (),
    )


def test_r_metrics_drawdown_and_streaks() -> None:
    metrics = performance_metrics((ledger(0, "1"), ledger(1, "-2"), ledger(2, "-1"), ledger(3, "2")))
    assert metrics.total_r == 0
    assert metrics.expectancy_r == 0
    assert metrics.maximum_drawdown_r == D("3")
    assert metrics.maximum_consecutive_losses == 2
    assert metrics.win_rate == D("50")


def test_breakdown_grouping_and_replay_determinism() -> None:
    entries = (ledger(1, "-1", setup=SetupType.FLAT_TOP_BREAKOUT), ledger(0, "2"))
    first = grouped_results(entries, key=lambda item: item.setup.value)
    second = grouped_results(tuple(reversed(entries)), key=lambda item: item.setup.value)
    assert first == second
    assert {item.bucket for item in first} == {"BULL_FLAG", "FLAT_TOP_BREAKOUT"}


def test_captured_dataset_hash_and_multisession_coverage() -> None:
    dataset = load_dataset(Path("data/warrior_momentum_v1_validation"))
    assert dataset.sha256 == "2c0ddf816eb82a40c14aebbd836e7bc403fe6aa1fbc324614fe88ee87801a41d"
    assert len(dataset.bars) == 19800
    assert min(bar.timestamp.date() for bar in dataset.bars) < max(bar.timestamp.date() for bar in dataset.bars)
    assert "UNAVAILABLE" in dataset.catalyst_evidence
