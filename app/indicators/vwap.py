from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def vwap(
    highs: Sequence[Decimal | int | float], lows: Sequence[Decimal | int | float], closes: Sequence[Decimal | int | float], volumes: Sequence[Decimal | int | float]
) -> list[Decimal | None]:
    if len({len(highs), len(lows), len(closes), len(volumes)}) != 1:
        raise ValueError("price and volume series must have equal lengths")
    cumulative_value = Decimal(0)
    cumulative_volume = Decimal(0)
    result: list[Decimal | None] = []
    for high, low, close, volume in zip(highs, lows, closes, volumes, strict=True):
        volume = _decimal(volume)
        if volume < 0:
            raise ValueError("volume cannot be negative")
        typical_price = (_decimal(high) + _decimal(low) + _decimal(close)) / Decimal(3)
        cumulative_value += typical_price * volume
        cumulative_volume += volume
        result.append(cumulative_value / cumulative_volume if cumulative_volume else None)
    return result


def _decimal(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
