"""Deterministic paper-validation models and analytics for the frozen V1 rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum
from statistics import median

from .models import (
    MinuteBar, MomentumEntrySignal, PaperExit, ReasonCode, SetupType, StopModel,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class ExecutionScenarioName(StrEnum):
    IDEALIZED = "IDEALIZED"
    BASELINE = "BASELINE"
    CONSERVATIVE = "CONSERVATIVE"


@dataclass(frozen=True, slots=True)
class ExecutionScenario:
    name: ExecutionScenarioName
    slippage_per_share: Decimal
    spread_percent: Decimal
    fill_ratio: Decimal
    entry_delay_bars: int

    def __post_init__(self) -> None:
        if self.slippage_per_share < 0 or self.spread_percent < 0:
            raise ValueError("execution costs cannot be negative")
        if not ZERO < self.fill_ratio <= 1:
            raise ValueError("fill_ratio must be in (0, 1]")
        if self.entry_delay_bars < 0:
            raise ValueError("entry delay cannot be negative")


IDEALIZED = ExecutionScenario(ExecutionScenarioName.IDEALIZED, ZERO, ZERO, Decimal("1"), 0)
BASELINE = ExecutionScenario(ExecutionScenarioName.BASELINE, Decimal("0.01"), Decimal("0.50"), Decimal("0.90"), 1)
CONSERVATIVE = ExecutionScenario(ExecutionScenarioName.CONSERVATIVE, Decimal("0.03"), Decimal("1.00"), Decimal("0.70"), 1)


@dataclass(frozen=True, slots=True)
class ReplayLedgerEntry:
    scenario: ExecutionScenarioName
    timestamp: datetime
    exit_timestamp: datetime
    symbol: str
    setup: SetupType
    stop_model: StopModel
    momentum_score: Decimal
    entry: Decimal
    stop: Decimal
    risk_per_share: Decimal
    position_size: int
    targets: tuple[Decimal, ...]
    exits: tuple[PaperExit, ...]
    realized_r: Decimal
    mae_r: Decimal
    mfe_r: Decimal
    hold_seconds: Decimal
    catalyst_state: str
    relative_volume: Decimal
    float_bucket: str
    price_bucket: str
    session: str
    reasoning_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    sample_size: int
    wins: int
    losses: int
    scratches: int
    win_rate: Decimal
    loss_rate: Decimal
    scratch_rate: Decimal
    profit_factor: Decimal | None
    expectancy_r: Decimal | None
    average_r: Decimal | None
    median_r: Decimal | None
    total_r: Decimal
    maximum_drawdown_r: Decimal
    average_mae_r: Decimal | None
    average_mfe_r: Decimal | None
    average_hold_seconds: Decimal | None
    median_hold_seconds: Decimal | None
    largest_win_r: Decimal | None
    largest_loss_r: Decimal | None
    maximum_consecutive_wins: int
    maximum_consecutive_losses: int


@dataclass(frozen=True, slots=True)
class GroupResult:
    bucket: str
    candidate_count: int
    trade_count: int
    metrics: PerformanceMetrics


def simulate_managed_trade(
    signal: MomentumEntrySignal,
    future_bars: tuple[MinuteBar, ...],
    *,
    requested_quantity: int,
    scenario: ExecutionScenario,
) -> ReplayLedgerEntry | None:
    """Simulate V1 partial exits with stop-first same-bar resolution.

    Signal timestamps are decision times. Bars use opening timestamps, so only
    bars opening at or after the decision can fill. Delayed entry skips the
    configured number of otherwise eligible bars.
    """
    if requested_quantity <= 0:
        raise ValueError("requested_quantity must be positive")
    eligible = tuple(sorted(
        (bar for bar in future_bars if bar.symbol.strip().upper() == signal.symbol
         and bar.timestamp >= signal.timestamp),
        key=lambda bar: bar.timestamp,
    ))
    if len(eligible) <= scenario.entry_delay_bars:
        return None
    bars = eligible[scenario.entry_delay_bars:]
    fill_bar = bars[0]
    half_spread = signal.entry_trigger * scenario.spread_percent / HUNDRED / Decimal("2")
    entry = max(signal.entry_trigger, fill_bar.open) + half_spread + scenario.slippage_per_share
    if fill_bar.high < entry:
        return None
    quantity = int((Decimal(requested_quantity) * scenario.fill_ratio).to_integral_value(rounding=ROUND_FLOOR))
    if quantity <= 0:
        return None
    first_quantity = int((Decimal(quantity) * Decimal("0.50")).to_integral_value(rounding=ROUND_FLOOR))
    second_quantity = int((Decimal(quantity) * Decimal("0.25")).to_integral_value(rounding=ROUND_FLOOR))
    remaining = quantity
    active_stop = signal.stop_price
    exits: list[PaperExit] = []
    lows: list[Decimal] = []
    highs: list[Decimal] = []
    exit_timestamp = bars[-1].timestamp + timedelta(minutes=1)
    prior_bar: MinuteBar | None = None
    first_taken = second_taken = False
    exit_cost = half_spread + scenario.slippage_per_share

    for bar in bars:
        lows.append(bar.low)
        highs.append(bar.high)
        # Same-bar ambiguity is deliberately resolved against the strategy.
        if bar.low <= active_stop:
            exits.append(PaperExit("STOP", active_stop - exit_cost, remaining))
            remaining = 0
            exit_timestamp = bar.timestamp + timedelta(minutes=1)
            break
        if not first_taken and bar.high >= signal.target_levels[0]:
            amount = min(first_quantity, remaining)
            exits.append(PaperExit("FIRST_TARGET", signal.target_levels[0] - exit_cost, amount))
            remaining -= amount
            first_taken = True
            active_stop = max(active_stop, signal.entry_trigger)
        if remaining and not second_taken and bar.high >= signal.target_levels[1]:
            amount = min(second_quantity, remaining)
            exits.append(PaperExit("SECOND_TARGET", signal.target_levels[1] - exit_cost, amount))
            remaining -= amount
            second_taken = True
        if remaining and bar.high >= signal.target_levels[2]:
            exits.append(PaperExit("RUNNER_TARGET", signal.target_levels[2] - exit_cost, remaining))
            remaining = 0
            exit_timestamp = bar.timestamp + timedelta(minutes=1)
            break
        # A structural trail may only use a bar completed before the next bar.
        if first_taken and prior_bar is not None and prior_bar.low < bar.close:
            active_stop = max(active_stop, prior_bar.low)
        prior_bar = bar

    if remaining:
        exits.append(PaperExit("END_OF_DATA", bars[-1].close - exit_cost, remaining))
    risk_dollars = signal.risk_per_share * quantity
    pnl = sum(((item.price - entry) * item.quantity for item in exits), ZERO)
    realized_r = pnl / risk_dollars
    return ReplayLedgerEntry(
        scenario=scenario.name, timestamp=fill_bar.timestamp,
        exit_timestamp=exit_timestamp, symbol=signal.symbol,
        setup=signal.setup_type, stop_model=signal.stop_model,
        momentum_score=signal.momentum_score, entry=entry,
        stop=signal.stop_price, risk_per_share=signal.risk_per_share,
        position_size=quantity, targets=signal.target_levels,
        exits=tuple(exits), realized_r=realized_r,
        mae_r=(min(lows) - entry) / signal.risk_per_share,
        mfe_r=(max(highs) - entry) / signal.risk_per_share,
        hold_seconds=Decimal(str((exit_timestamp - fill_bar.timestamp).total_seconds())),
        catalyst_state=signal.catalyst_state.value,
        relative_volume=signal.relative_volume,
        float_bucket=float_bucket(signal.float_shares),
        price_bucket=price_bucket(signal.reference_price),
        session=signal.session,
        reasoning_codes=tuple(code.value for code in signal.reasoning_codes),
    )


def performance_metrics(entries: tuple[ReplayLedgerEntry, ...]) -> PerformanceMetrics:
    ordered = tuple(sorted(entries, key=lambda item: (item.timestamp, item.symbol)))
    values = tuple(item.realized_r for item in ordered)
    wins = tuple(value for value in values if value > 0)
    losses = tuple(value for value in values if value < 0)
    scratches = tuple(value for value in values if value == 0)
    count = len(values)
    gross_profit = sum(wins, ZERO)
    gross_loss = abs(sum(losses, ZERO))
    holds = tuple(item.hold_seconds for item in ordered)
    return PerformanceMetrics(
        sample_size=count, wins=len(wins), losses=len(losses), scratches=len(scratches),
        win_rate=_rate(len(wins), count), loss_rate=_rate(len(losses), count),
        scratch_rate=_rate(len(scratches), count),
        profit_factor=None if gross_loss == 0 else gross_profit / gross_loss,
        expectancy_r=_average(values), average_r=_average(values),
        median_r=_median(values), total_r=sum(values, ZERO),
        maximum_drawdown_r=_maximum_drawdown(values),
        average_mae_r=_average(tuple(item.mae_r for item in ordered)),
        average_mfe_r=_average(tuple(item.mfe_r for item in ordered)),
        average_hold_seconds=_average(holds), median_hold_seconds=_median(holds),
        largest_win_r=max(wins, default=None), largest_loss_r=min(losses, default=None),
        maximum_consecutive_wins=_maximum_streak(values, positive=True),
        maximum_consecutive_losses=_maximum_streak(values, positive=False),
    )


def grouped_results(
    entries: tuple[ReplayLedgerEntry, ...],
    *,
    candidate_buckets: tuple[str, ...] = (),
    key,
) -> tuple[GroupResult, ...]:
    groups: dict[str, list[ReplayLedgerEntry]] = {}
    for entry in entries:
        groups.setdefault(str(key(entry)), []).append(entry)
    counts: dict[str, int] = {}
    for bucket in candidate_buckets:
        counts[bucket] = counts.get(bucket, 0) + 1
    names = sorted(set(groups) | set(counts))
    return tuple(GroupResult(name, counts.get(name, 0), len(groups.get(name, ())),
                             performance_metrics(tuple(groups.get(name, ())))) for name in names)


def score_bucket(score: Decimal) -> str:
    if score < 25:
        return "<25"
    if score < 45:
        return "25-44"
    if score < 60:
        return "45-59"
    if score < 70:
        return "60-69"
    if score < 80:
        return "70-79"
    if score < 90:
        return "80-89"
    return "90-100"


def float_bucket(value: Decimal | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value <= Decimal("5000000"):
        return "<=5M"
    if value <= Decimal("10000000"):
        return "5-10M"
    if value <= Decimal("20000000"):
        return "10-20M"
    if value <= Decimal("50000000"):
        return "20-50M"
    return ">50M"


def rvol_bucket(value: Decimal) -> str:
    if value < 2:
        return "<2x"
    if value < 5:
        return "2-5x"
    if value < 10:
        return "5-10x"
    if value < 25:
        return "10-25x"
    return "25x+"


def price_bucket(value: Decimal) -> str:
    if value < 1:
        return "<$1"
    if value < 2:
        return "$1-$2"
    if value < 5:
        return "$2-$5"
    if value < 10:
        return "$5-$10"
    if value <= 20:
        return "$10-$20"
    return ">$20"


def break_even_slippage(entries: tuple[ReplayLedgerEntry, ...]) -> Decimal | None:
    if not entries:
        return None
    total_r = sum((item.realized_r for item in entries), ZERO)
    sensitivity = sum((Decimal("2") / item.risk_per_share for item in entries), ZERO)
    return None if sensitivity <= 0 else max(ZERO, total_r / sensitivity)


def _average(values: tuple[Decimal, ...]) -> Decimal | None:
    return None if not values else sum(values, ZERO) / len(values)


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    return None if not values else Decimal(str(median(values)))


def _rate(count: int, total: int) -> Decimal:
    return ZERO if total == 0 else Decimal(count) / total * HUNDRED


def _maximum_drawdown(values: tuple[Decimal, ...]) -> Decimal:
    equity = peak = result = ZERO
    for value in values:
        equity += value
        peak = max(peak, equity)
        result = max(result, peak - equity)
    return result


def _maximum_streak(values: tuple[Decimal, ...], *, positive: bool) -> int:
    best = current = 0
    for value in values:
        matches = value > 0 if positive else value < 0
        current = current + 1 if matches else 0
        best = max(best, current)
    return best


__all__ = [
    "ExecutionScenarioName", "ExecutionScenario", "IDEALIZED", "BASELINE",
    "CONSERVATIVE", "ReplayLedgerEntry", "PerformanceMetrics", "GroupResult",
    "simulate_managed_trade", "performance_metrics", "grouped_results",
    "score_bucket", "float_bucket", "rvol_bucket", "price_bucket",
    "break_even_slippage",
]
