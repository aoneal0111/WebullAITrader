from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


class ExitAction(str, Enum):
    HOLD = "HOLD"
    RAISE_STOP = "RAISE_STOP"
    EXIT = "EXIT"


@dataclass(frozen=True, slots=True)
class TrailingExitConfig:
    """
    Configuration for protecting a long position while allowing gains to run.

    activation_gain_percent:
        Profit required before the trailing stop activates.

    trailing_distance_percent:
        Distance maintained between the highest observed price and the stop.

    break_even_trigger_percent:
        Profit required before the stop may be raised to the entry price.

    minimum_stop_raise_percent:
        Prevents insignificant stop updates.
    """

    activation_gain_percent: Decimal = Decimal("3.00")
    trailing_distance_percent: Decimal = Decimal("2.00")
    break_even_trigger_percent: Decimal = Decimal("1.50")
    minimum_stop_raise_percent: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        _require_nonnegative(
            self.activation_gain_percent,
            "activation_gain_percent",
        )
        _require_positive(
            self.trailing_distance_percent,
            "trailing_distance_percent",
        )
        _require_nonnegative(
            self.break_even_trigger_percent,
            "break_even_trigger_percent",
        )
        _require_nonnegative(
            self.minimum_stop_raise_percent,
            "minimum_stop_raise_percent",
        )

        if self.trailing_distance_percent >= HUNDRED:
            raise ValueError(
                "trailing_distance_percent must be less than 100"
            )


@dataclass(frozen=True, slots=True)
class ManagedLongPosition:
    symbol: str
    entry_price: Decimal
    quantity: Decimal
    highest_price: Decimal
    protective_stop: Decimal | None = None
    trailing_activated: bool = False

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("symbol is required")

        object.__setattr__(self, "symbol", symbol)

        _require_positive(self.entry_price, "entry_price")
        _require_positive(self.quantity, "quantity")
        _require_positive(self.highest_price, "highest_price")

        if self.highest_price < self.entry_price:
            raise ValueError(
                "highest_price cannot be below entry_price"
            )

        if self.protective_stop is not None:
            _require_positive(
                self.protective_stop,
                "protective_stop",
            )


@dataclass(frozen=True, slots=True)
class ExitDecision:
    action: ExitAction
    reason: str
    current_price: Decimal
    gain_percent: Decimal
    previous_stop: Decimal | None
    recommended_stop: Decimal | None
    position: ManagedLongPosition


def evaluate_long_position(
    position: ManagedLongPosition,
    current_price: Decimal,
    config: TrailingExitConfig | None = None,
) -> ExitDecision:
    """
    Evaluate one price observation for a long position.

    Safety properties:
    - The highest observed price can only increase.
    - The protective stop can only increase.
    - The stop is never moved farther away from the market.
    - An exit occurs when price reaches or falls below the active stop.
    """

    if config is None:
        config = TrailingExitConfig()

    _require_positive(current_price, "current_price")

    highest_price = max(
        position.highest_price,
        current_price,
    )

    gain_percent = _percent_change(
        position.entry_price,
        current_price,
    )

    if (
        position.protective_stop is not None
        and current_price <= position.protective_stop
    ):
        updated = ManagedLongPosition(
            symbol=position.symbol,
            entry_price=position.entry_price,
            quantity=position.quantity,
            highest_price=highest_price,
            protective_stop=position.protective_stop,
            trailing_activated=position.trailing_activated,
        )

        return ExitDecision(
            action=ExitAction.EXIT,
            reason="Current price reached the protective stop.",
            current_price=current_price,
            gain_percent=gain_percent,
            previous_stop=position.protective_stop,
            recommended_stop=position.protective_stop,
            position=updated,
        )

    trailing_activated = (
        position.trailing_activated
        or _percent_change(
            position.entry_price,
            highest_price,
        )
        >= config.activation_gain_percent
    )

    candidate_stop = position.protective_stop

    if (
        gain_percent >= config.break_even_trigger_percent
        and (
            candidate_stop is None
            or candidate_stop < position.entry_price
        )
    ):
        candidate_stop = position.entry_price

    if trailing_activated:
        trailing_stop = highest_price * (
            ONE - (
                config.trailing_distance_percent / HUNDRED
            )
        )

        if (
            candidate_stop is None
            or trailing_stop > candidate_stop
        ):
            candidate_stop = trailing_stop

    candidate_stop = _rounded_price(candidate_stop)

    updated = ManagedLongPosition(
        symbol=position.symbol,
        entry_price=position.entry_price,
        quantity=position.quantity,
        highest_price=highest_price,
        protective_stop=(
            _higher_stop(
                position.protective_stop,
                candidate_stop,
            )
        ),
        trailing_activated=trailing_activated,
    )

    if _stop_was_meaningfully_raised(
        previous_stop=position.protective_stop,
        new_stop=updated.protective_stop,
        minimum_raise_percent=(
            config.minimum_stop_raise_percent
        ),
    ):
        return ExitDecision(
            action=ExitAction.RAISE_STOP,
            reason=(
                "Price advanced and the protective stop "
                "can be raised without reducing protection."
            ),
            current_price=current_price,
            gain_percent=gain_percent,
            previous_stop=position.protective_stop,
            recommended_stop=updated.protective_stop,
            position=updated,
        )

    return ExitDecision(
        action=ExitAction.HOLD,
        reason=(
            "The position remains above its protective stop "
            "and no meaningful stop increase is required."
        ),
        current_price=current_price,
        gain_percent=gain_percent,
        previous_stop=position.protective_stop,
        recommended_stop=updated.protective_stop,
        position=updated,
    )


def _stop_was_meaningfully_raised(
    *,
    previous_stop: Decimal | None,
    new_stop: Decimal | None,
    minimum_raise_percent: Decimal,
) -> bool:
    if new_stop is None:
        return False

    if previous_stop is None:
        return True

    if new_stop <= previous_stop:
        return False

    required_raise = previous_stop * (
        minimum_raise_percent / HUNDRED
    )

    return new_stop - previous_stop >= required_raise


def _higher_stop(
    existing: Decimal | None,
    candidate: Decimal | None,
) -> Decimal | None:
    if existing is None:
        return candidate

    if candidate is None:
        return existing

    return max(existing, candidate)


def _percent_change(
    starting_price: Decimal,
    current_price: Decimal,
) -> Decimal:
    return (
        (current_price - starting_price)
        / starting_price
        * HUNDRED
    ).quantize(Decimal("0.01"))


def _rounded_price(
    value: Decimal | None,
) -> Decimal | None:
    if value is None:
        return None

    return value.quantize(Decimal("0.01"))


def _require_positive(
    value: Decimal,
    name: str,
) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= ZERO
    ):
        raise ValueError(
            f"{name} must be a positive finite Decimal"
        )


def _require_nonnegative(
    value: Decimal,
    name: str,
) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < ZERO
    ):
        raise ValueError(
            f"{name} must be a nonnegative finite Decimal"
        )

