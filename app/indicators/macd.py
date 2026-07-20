from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.indicators.ema import ema


@dataclass(frozen=True, slots=True)
class MACDResult:
    macd: tuple[Decimal, ...]
    signal: tuple[Decimal, ...]
    histogram: tuple[Decimal, ...]


def macd(
    closes: Sequence[Decimal | int | float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9
) -> MACDResult:
    if min(fast_period, slow_period, signal_period) <= 0:
        raise ValueError("periods must be greater than zero")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    line = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow, strict=True)]
    signal = ema(line, signal_period)
    histogram = [value - signal_value for value, signal_value in zip(line, signal, strict=True)]
    return MACDResult(tuple(line), tuple(signal), tuple(histogram))
