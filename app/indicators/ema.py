from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def ema(values: Sequence[Decimal | int | float], period: int) -> list[Decimal]:
    """Return an EMA series, seeded with the first value."""
    if period <= 0:
        raise ValueError("period must be greater than zero")
    if not values:
        return []
    multiplier = Decimal(2) / Decimal(period + 1)
    result = [_decimal(values[0])]
    for value in values[1:]:
        result.append((_decimal(value) - result[-1]) * multiplier + result[-1])
    return result


def _decimal(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
