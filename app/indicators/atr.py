from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def atr(
    highs: Sequence[Decimal | int | float], lows: Sequence[Decimal | int | float], closes: Sequence[Decimal | int | float], period: int = 14
) -> list[Decimal | None]:
    """Return Wilder average true range aligned with the input bars."""
    _validate_bars(highs, lows, closes)
    if period <= 0:
        raise ValueError("period must be greater than zero")
    result: list[Decimal | None] = [None] * len(closes)
    if not closes:
        return result
    true_ranges = [_decimal(highs[0]) - _decimal(lows[0])]
    for index in range(1, len(closes)):
        true_ranges.append(
            max(
                _decimal(highs[index]) - _decimal(lows[index]),
                abs(_decimal(highs[index]) - _decimal(closes[index - 1])),
                abs(_decimal(lows[index]) - _decimal(closes[index - 1])),
            )
        )
    if len(true_ranges) < period:
        return result
    current = sum(true_ranges[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = current
    for index in range(period, len(true_ranges)):
        current = (current * Decimal(period - 1) + true_ranges[index]) / Decimal(period)
        result[index] = current
    return result


def _validate_bars(*series: Sequence[Decimal | int | float]) -> None:
    if len({len(values) for values in series}) != 1:
        raise ValueError("highs, lows, and closes must have equal lengths")
    if any(_decimal(high) < _decimal(low) for high, low in zip(series[0], series[1], strict=True)):
        raise ValueError("high must be greater than or equal to low")


def _decimal(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
