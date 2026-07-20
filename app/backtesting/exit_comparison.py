from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from app.backtesting.models import HistoricalFrame
from app.trade_management import (
    ExitAction,
    ManagedLongPosition,
    TrailingExitConfig,
    evaluate_long_position,
)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


class HistoricalExitMethod(StrEnum):
    FIXED_TARGET = "FIXED_TARGET"
    TRAILING_STOP = "TRAILING_STOP"
    END_OF_DATA = "END_OF_DATA"


@dataclass(frozen=True, slots=True)
class HistoricalTradeResult:
    symbol: str
    entry_index: int
    exit_index: int
    entry_price: Decimal
    exit_price: Decimal
    highest_price: Decimal
    quantity: Decimal
    realized_pnl: Decimal
    return_percent: Decimal
    holding_candles: int
    exit_method: HistoricalExitMethod


@dataclass(frozen=True, slots=True)
class ExitStrategyMetrics:
    number_of_trades: int
    winning_trades: int
    losing_trades: int
    break_even_trades: int
    total_realized_pnl: Decimal
    total_return_percent: Decimal
    win_rate: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal | None
    maximum_drawdown_percent: Decimal
    average_holding_candles: Decimal
    trades: tuple[HistoricalTradeResult, ...]


@dataclass(frozen=True, slots=True)
class BacktestExitComparison:
    fixed_target: ExitStrategyMetrics
    trailing_stop: ExitStrategyMetrics

    @property
    def return_improvement_percent(self) -> Decimal:
        return (
            self.trailing_stop.total_return_percent
            - self.fixed_target.total_return_percent
        )

    @property
    def pnl_improvement(self) -> Decimal:
        return (
            self.trailing_stop.total_realized_pnl
            - self.fixed_target.total_realized_pnl
        )


def evaluate_fixed_target_entries(
    frames: tuple[HistoricalFrame, ...],
    entry_indices: Iterable[int],
    *,
    target_gain_percent: Decimal = Decimal("5"),
    quantity: Decimal = ONE,
) -> ExitStrategyMetrics:
    _validate_inputs(frames, target_gain_percent, quantity)

    trades = tuple(
        _simulate_fixed_target_trade(
            frames,
            entry_index,
            target_gain_percent,
            quantity,
        )
        for entry_index in entry_indices
    )

    return calculate_exit_strategy_metrics(trades)


def evaluate_trailing_entries(
    frames: tuple[HistoricalFrame, ...],
    entry_indices: Iterable[int],
    *,
    config: TrailingExitConfig | None = None,
    quantity: Decimal = ONE,
) -> ExitStrategyMetrics:
    _validate_inputs(frames, ONE, quantity)
    trailing_config = config or TrailingExitConfig()

    trades = tuple(
        _simulate_trailing_trade(
            frames,
            entry_index,
            trailing_config,
            quantity,
        )
        for entry_index in entry_indices
    )

    return calculate_exit_strategy_metrics(trades)


def compare_historical_exit_methods(
    frames: tuple[HistoricalFrame, ...],
    entry_indices: Iterable[int],
    *,
    fixed_target_percent: Decimal = Decimal("5"),
    trailing_config: TrailingExitConfig | None = None,
    quantity: Decimal = ONE,
) -> BacktestExitComparison:
    indices = tuple(entry_indices)

    return BacktestExitComparison(
        fixed_target=evaluate_fixed_target_entries(
            frames,
            indices,
            target_gain_percent=fixed_target_percent,
            quantity=quantity,
        ),
        trailing_stop=evaluate_trailing_entries(
            frames,
            indices,
            config=trailing_config,
            quantity=quantity,
        ),
    )


def calculate_exit_strategy_metrics(
    trades: tuple[HistoricalTradeResult, ...],
) -> ExitStrategyMetrics:
    if not trades:
        return ExitStrategyMetrics(
            0, 0, 0, 0,
            ZERO, ZERO, ZERO,
            None, None,
            ZERO, ZERO, (),
        )

    winners = tuple(t for t in trades if t.realized_pnl > ZERO)
    losers = tuple(t for t in trades if t.realized_pnl < ZERO)
    break_even = tuple(t for t in trades if t.realized_pnl == ZERO)

    gross_profit = sum(
        (trade.realized_pnl for trade in winners),
        ZERO,
    )
    gross_loss = abs(sum(
        (trade.realized_pnl for trade in losers),
        ZERO,
    ))

    total_pnl = sum(
        (trade.realized_pnl for trade in trades),
        ZERO,
    )
    total_return = sum(
        (trade.return_percent for trade in trades),
        ZERO,
    )
    count = Decimal(len(trades))

    return ExitStrategyMetrics(
        number_of_trades=len(trades),
        winning_trades=len(winners),
        losing_trades=len(losers),
        break_even_trades=len(break_even),
        total_realized_pnl=total_pnl,
        total_return_percent=total_return,
        win_rate=Decimal(len(winners)) / count * HUNDRED,
        profit_factor=(
            None if gross_loss == ZERO
            else gross_profit / gross_loss
        ),
        expectancy=total_pnl / count,
        maximum_drawdown_percent=_maximum_drawdown_percent(trades),
        average_holding_candles=(
            sum(
                (Decimal(t.holding_candles) for t in trades),
                ZERO,
            )
            / count
        ),
        trades=trades,
    )


def _simulate_fixed_target_trade(
    frames: tuple[HistoricalFrame, ...],
    entry_index: int,
    target_gain_percent: Decimal,
    quantity: Decimal,
) -> HistoricalTradeResult:
    _validate_entry_index(frames, entry_index)

    entry = frames[entry_index]
    entry_price = entry.execution_ask
    highest_price = entry_price
    target_price = entry_price * (
        ONE + target_gain_percent / HUNDRED
    )

    for index in range(entry_index + 1, len(frames)):
        candle = frames[index].candle
        highest_price = max(highest_price, candle.high)

        if candle.high >= target_price:
            return _trade_result(
                entry.candle.symbol,
                entry_index,
                index,
                entry_price,
                target_price,
                highest_price,
                quantity,
                HistoricalExitMethod.FIXED_TARGET,
            )

    final_index = len(frames) - 1

    return _trade_result(
        entry.candle.symbol,
        entry_index,
        final_index,
        entry_price,
        frames[final_index].execution_bid,
        highest_price,
        quantity,
        HistoricalExitMethod.END_OF_DATA,
    )


def _simulate_trailing_trade(
    frames: tuple[HistoricalFrame, ...],
    entry_index: int,
    config: TrailingExitConfig,
    quantity: Decimal,
) -> HistoricalTradeResult:
    _validate_entry_index(frames, entry_index)

    entry = frames[entry_index]
    entry_price = entry.execution_ask

    position = ManagedLongPosition(
        symbol=entry.candle.symbol,
        entry_price=entry_price,
        quantity=quantity,
        highest_price=entry_price,
    )

    for index in range(entry_index + 1, len(frames)):
        candle = frames[index].candle

        decision = evaluate_long_position(
            position,
            candle.high,
            config,
        )
        position = decision.position

        if (
            position.protective_stop is not None
            and candle.low <= position.protective_stop
        ):
            return _trade_result(
                entry.candle.symbol,
                entry_index,
                index,
                entry_price,
                position.protective_stop,
                position.highest_price,
                quantity,
                HistoricalExitMethod.TRAILING_STOP,
            )

        decision = evaluate_long_position(
            position,
            candle.close,
            config,
        )
        position = decision.position

        if decision.action is ExitAction.EXIT:
            if position.protective_stop is None:
                raise RuntimeError(
                    "EXIT decision requires an active stop"
                )

            return _trade_result(
                entry.candle.symbol,
                entry_index,
                index,
                entry_price,
                position.protective_stop,
                position.highest_price,
                quantity,
                HistoricalExitMethod.TRAILING_STOP,
            )

    final_index = len(frames) - 1

    return _trade_result(
        entry.candle.symbol,
        entry_index,
        final_index,
        entry_price,
        frames[final_index].execution_bid,
        position.highest_price,
        quantity,
        HistoricalExitMethod.END_OF_DATA,
    )


def _trade_result(
    symbol: str,
    entry_index: int,
    exit_index: int,
    entry_price: Decimal,
    exit_price: Decimal,
    highest_price: Decimal,
    quantity: Decimal,
    method: HistoricalExitMethod,
) -> HistoricalTradeResult:
    pnl = (exit_price - entry_price) * quantity

    return HistoricalTradeResult(
        symbol=symbol,
        entry_index=entry_index,
        exit_index=exit_index,
        entry_price=entry_price,
        exit_price=exit_price,
        highest_price=highest_price,
        quantity=quantity,
        realized_pnl=pnl,
        return_percent=(
            (exit_price - entry_price)
            / entry_price
            * HUNDRED
        ),
        holding_candles=exit_index - entry_index,
        exit_method=method,
    )


def _maximum_drawdown_percent(
    trades: tuple[HistoricalTradeResult, ...],
) -> Decimal:
    cumulative = ZERO
    peak = ZERO
    maximum = ZERO

    for trade in trades:
        cumulative += trade.return_percent
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)

    return maximum


def _validate_inputs(
    frames: tuple[HistoricalFrame, ...],
    target_gain_percent: Decimal,
    quantity: Decimal,
) -> None:
    if len(frames) < 2:
        raise ValueError(
            "at least two historical frames are required"
        )

    if (
        not isinstance(target_gain_percent, Decimal)
        or not target_gain_percent.is_finite()
        or target_gain_percent <= ZERO
    ):
        raise ValueError(
            "target_gain_percent must be a positive finite Decimal"
        )

    if (
        not isinstance(quantity, Decimal)
        or not quantity.is_finite()
        or quantity <= ZERO
    ):
        raise ValueError(
            "quantity must be a positive finite Decimal"
        )


def _validate_entry_index(
    frames: tuple[HistoricalFrame, ...],
    entry_index: int,
) -> None:
    if isinstance(entry_index, bool) or not isinstance(entry_index, int):
        raise TypeError("entry_index must be an integer")

    if not 0 <= entry_index < len(frames) - 1:
        raise ValueError(
            "entry_index must leave at least one future frame"
        )

