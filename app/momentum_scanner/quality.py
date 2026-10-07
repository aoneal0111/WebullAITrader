"""Transparent momentum-participation and execution-quality features.

These functions rank observations; they never authorize execution. Quote
validity and provider freshness remain separate fail-closed execution rails.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import ExecutionQuality, ParticipationBand

ZERO = Decimal("0")
ONE = Decimal("1")


def _clip(value: Decimal) -> Decimal:
    return max(ZERO, min(ONE, value))


def rvol_quality(relative_volume: Decimal | None) -> tuple[Decimal, ParticipationBand]:
    if relative_volume is None:
        return ZERO, ParticipationBand.UNKNOWN
    if relative_volume < Decimal("1"):
        return _clip(relative_volume * Decimal("0.15")), ParticipationBand.WEAK
    if relative_volume < Decimal("2"):
        return Decimal("0.15") + (relative_volume - ONE) * Decimal("0.20"), ParticipationBand.MODERATE
    if relative_volume < Decimal("5"):
        return Decimal("0.35") + (relative_volume - Decimal("2")) / Decimal("3") * Decimal("0.40"), ParticipationBand.STRONG
    return min(ONE, Decimal("0.75") + (relative_volume - Decimal("5")) / Decimal("20")), ParticipationBand.VERY_STRONG


def spread_quality(
    spread_percent: Decimal | None,
    *,
    normal_percent: Decimal,
    catastrophic_percent: Decimal = Decimal("5"),
) -> tuple[Decimal, ExecutionQuality, str | None]:
    if spread_percent is None or spread_percent < ZERO:
        return ZERO, ExecutionQuality.TEMPORARILY_BLOCKED, "QUOTE_UNAVAILABLE"
    if spread_percent >= catastrophic_percent:
        return ZERO, ExecutionQuality.TEMPORARILY_BLOCKED, "SPREAD_UNEXECUTABLE"
    if spread_percent <= normal_percent / Decimal("2"):
        return ONE, ExecutionQuality.EXCELLENT, None
    if spread_percent <= normal_percent:
        return Decimal("0.75"), ExecutionQuality.GOOD, None
    if spread_percent <= normal_percent * Decimal("2"):
        return Decimal("0.50"), ExecutionQuality.MARGINAL, "SPREAD_ELEVATED"
    return Decimal("0.20"), ExecutionQuality.POOR, "WAIT_FOR_SPREAD_IMPROVEMENT"


@dataclass(frozen=True, slots=True)
class PriceMotion:
    cents_1m: Decimal | None = None
    percent_1m: Decimal | None = None
    cents_5m: Decimal | None = None
    percent_5m: Decimal | None = None
    acceleration_percent: Decimal | None = None


def velocity_attention(cents_per_minute: Decimal | None, percent_per_minute: Decimal | None) -> Decimal:
    """Combine cents and percentage velocity without making either a buy rule."""
    cents = ZERO if cents_per_minute is None else max(ZERO, cents_per_minute)
    percent = ZERO if percent_per_minute is None else max(ZERO, percent_per_minute)
    cents_score = _clip(cents / Decimal("0.10"))
    percent_score = _clip(percent / Decimal("2"))
    return _clip(cents_score * Decimal("0.45") + percent_score * Decimal("0.55"))


def momentum_priority(
    *, percentage_change: Decimal, rvol_score: Decimal,
    dollar_volume: Decimal, velocity_score: Decimal,
    spread_score: Decimal, catalyst_present: bool,
) -> tuple[Decimal, tuple[tuple[str, str], ...]]:
    move = _clip(max(ZERO, percentage_change) / Decimal("100"))
    liquidity = _clip(max(ZERO, dollar_volume) / Decimal("10000000"))
    catalyst = ONE if catalyst_present else ZERO
    parts = (
        ("price_momentum", move, Decimal("0.30")),
        ("participation", _clip(rvol_score), Decimal("0.20")),
        ("dollar_liquidity", liquidity, Decimal("0.15")),
        ("price_velocity", _clip(velocity_score), Decimal("0.20")),
        ("execution_quality", _clip(spread_score), Decimal("0.10")),
        ("catalyst", catalyst, Decimal("0.05")),
    )
    total = sum((value * weight for _name, value, weight in parts), ZERO) * Decimal("100")
    visible = tuple((name, format(value, "f")) for name, value, _weight in parts)
    return total, visible


def reevaluation_cadence_ms(priority: Decimal, velocity_score: Decimal) -> int:
    """Bounded local attention hint; it never schedules provider polling."""
    if priority >= Decimal("75") or velocity_score >= Decimal("0.8"):
        return 100
    if priority >= Decimal("50") or velocity_score >= Decimal("0.45"):
        return 250
    return 1000


__all__ = [
    "PriceMotion", "momentum_priority", "reevaluation_cadence_ms",
    "rvol_quality", "spread_quality", "velocity_attention",
]
