from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BollingerBands:
    middle: tuple[Decimal | None, ...]
    upper: tuple[Decimal | None, ...]
    lower: tuple[Decimal | None, ...]


def bollinger_bands(
    closes: Sequence[Decimal | int | float], period: int = 20, standard_deviations: Decimal | int | float = Decimal(2)
) -> BollingerBands:
    if period <= 0 or standard_deviations < 0:
        raise ValueError("period must be positive and standard_deviations non-negative")
    deviations = _decimal(standard_deviations)
    middle: list[Decimal | None] = [None] * len(closes)
    upper: list[Decimal | None] = [None] * len(closes)
    lower: list[Decimal | None] = [None] * len(closes)
    for index in range(period - 1, len(closes)):
        window = [_decimal(value) for value in closes[index - period + 1 : index + 1]]
        mean = sum(window, Decimal(0)) / Decimal(period)
        variance = sum(((value - mean) ** 2 for value in window), Decimal(0)) / Decimal(period)
        deviation = variance.sqrt()
        middle[index] = mean
        upper[index] = mean + deviations * deviation
        lower[index] = mean - deviations * deviation
    return BollingerBands(tuple(middle), tuple(upper), tuple(lower))


def _decimal(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
