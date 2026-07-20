from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable

from app.trade_management import (
    ExitAction,
    ManagedLongPosition,
    TrailingExitConfig,
    evaluate_long_position,
)


HUNDRED = Decimal("100")


class ExitMethod(str, Enum):
    FIXED_TARGET = "FIXED_TARGET"
    TRAILING_STOP = "TRAILING_STOP"
    END_OF_DATA = "END_OF_DATA"


@dataclass(frozen=True, slots=True)
class TradeSimulationResult:
    exit_method: ExitMethod
    entry_price: Decimal
    exit_price: Decimal
    highest_price: Decimal
    gain_percent: Decimal
    observations_processed: int


@dataclass(frozen=True, slots=True)
class ExitComparison:
    fixed_target: TradeSimulationResult
    trailing_stop: TradeSimulationResult

    @property
    def trailing_improvement_percent(self) -> Decimal:
        return (
            self.trailing_stop.gain_percent
            - self.fixed_target.gain_percent
        ).quantize(Decimal("0.01"))


def simulate_fixed_profit_target(
    prices: Iterable[Decimal],
    *,
    target_gain_percent: Decimal,
) -> TradeSimulationResult:
    price_path = _validated_prices(prices)
    entry_price = price_path[0]
    highest_price = entry_price

    target_price = entry_price * (
        Decimal("1") + target_gain_percent / HUNDRED
    )

    for index, current_price in enumerate(
        price_path[1:],
        start=2,
    ):
        highest_price = max(highest_price, current_price)

        if current_price >= target_price:
            return TradeSimulationResult(
                exit_method=ExitMethod.FIXED_TARGET,
                entry_price=entry_price,
                exit_price=current_price,
                highest_price=highest_price,
                gain_percent=_gain_percent(
                    entry_price,
                    current_price,
                ),
                observations_processed=index,
            )

    exit_price = price_path[-1]

    return TradeSimulationResult(
        exit_method=ExitMethod.END_OF_DATA,
        entry_price=entry_price,
        exit_price=exit_price,
        highest_price=highest_price,
        gain_percent=_gain_percent(entry_price, exit_price),
        observations_processed=len(price_path),
    )


def simulate_trailing_stop(
    prices: Iterable[Decimal],
    *,
    config: TrailingExitConfig | None = None,
) -> TradeSimulationResult:
    price_path = _validated_prices(prices)
    entry_price = price_path[0]

    position = ManagedLongPosition(
        symbol="SIM",
        entry_price=entry_price,
        quantity=Decimal("1"),
        highest_price=entry_price,
    )

    for index, current_price in enumerate(
        price_path[1:],
        start=2,
    ):
        decision = evaluate_long_position(
            position,
            current_price,
            config,
        )

        position = decision.position

        if decision.action is ExitAction.EXIT:
            return TradeSimulationResult(
                exit_method=ExitMethod.TRAILING_STOP,
                entry_price=entry_price,
                exit_price=current_price,
                highest_price=position.highest_price,
                gain_percent=_gain_percent(
                    entry_price,
                    current_price,
                ),
                observations_processed=index,
            )

    exit_price = price_path[-1]

    return TradeSimulationResult(
        exit_method=ExitMethod.END_OF_DATA,
        entry_price=entry_price,
        exit_price=exit_price,
        highest_price=position.highest_price,
        gain_percent=_gain_percent(entry_price, exit_price),
        observations_processed=len(price_path),
    )


def compare_exit_methods(
    prices: Iterable[Decimal],
    *,
    fixed_target_percent: Decimal = Decimal("5"),
    trailing_config: TrailingExitConfig | None = None,
) -> ExitComparison:
    price_path = tuple(prices)

    return ExitComparison(
        fixed_target=simulate_fixed_profit_target(
            price_path,
            target_gain_percent=fixed_target_percent,
        ),
        trailing_stop=simulate_trailing_stop(
            price_path,
            config=trailing_config,
        ),
    )


def _validated_prices(
    prices: Iterable[Decimal],
) -> tuple[Decimal, ...]:
    values = tuple(prices)

    if len(values) < 2:
        raise ValueError(
            "at least two price observations are required"
        )

    for value in values:
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or value <= Decimal("0")
        ):
            raise ValueError(
                "all prices must be positive finite Decimals"
            )

    return values


def _gain_percent(
    entry_price: Decimal,
    exit_price: Decimal,
) -> Decimal:
    return (
        (exit_price - entry_price)
        / entry_price
        * HUNDRED
    ).quantize(Decimal("0.01"))
