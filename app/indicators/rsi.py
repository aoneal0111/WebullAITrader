from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def rsi(closes: Sequence[Decimal | int | float], period: int = 14) -> list[Decimal | None]:
    """Return Wilder RSI values aligned with the input closes."""
    if period <= 0:
        raise ValueError("period must be greater than zero")
    result: list[Decimal | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    changes = [_decimal(closes[i]) - _decimal(closes[i - 1]) for i in range(1, len(closes))]
    zero = Decimal(0)
    avg_gain = sum((max(change, zero) for change in changes[:period]), zero) / Decimal(period)
    avg_loss = sum((max(-change, zero) for change in changes[:period]), zero) / Decimal(period)
    result[period] = _rsi_value(avg_gain, avg_loss)
    for index in range(period, len(changes)):
        change = changes[index]
        avg_gain = (avg_gain * Decimal(period - 1) + max(change, zero)) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + max(-change, zero)) / Decimal(period)
        result[index + 1] = _rsi_value(avg_gain, avg_loss)
    return result


def _rsi_value(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    if avg_loss == 0:
        return Decimal(100) if avg_gain > 0 else Decimal(50)
    return Decimal(100) - Decimal(100) / (Decimal(1) + avg_gain / avg_loss)


def _decimal(value: Decimal | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))
