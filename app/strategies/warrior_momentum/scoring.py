"""Bounded, deterministic Atlas engineering score (not published Warrior weights)."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.momentum_scanner.models import CatalystStatus

from .configuration import ScoreWeights
from .models import MomentumScore

ZERO = Decimal("0")
ONE = Decimal("1")


def _linear(value: Decimal | None, weak: Decimal, strong: Decimal) -> Decimal:
    if value is None or value <= weak:
        return ZERO
    if value >= strong:
        return ONE
    return (value - weak) / (strong - weak)


def price_change_score(change_percent: Decimal | None) -> Decimal:
    return _linear(change_percent, Decimal("0"), Decimal("40"))


def relative_volume_score(relative_volume: Decimal | None) -> Decimal:
    # Piecewise anchors: 1x weak, 2x moderate, 5x strong, 10x very strong.
    if relative_volume is None or relative_volume <= 1:
        return ZERO
    anchors = ((Decimal("1"), ZERO), (Decimal("2"), Decimal("0.35")),
               (Decimal("5"), Decimal("0.75")), (Decimal("10"), ONE))
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if relative_volume <= x1:
            return y0 + (relative_volume - x0) / (x1 - x0) * (y1 - y0)
    return ONE


def float_score(float_shares: Decimal | None) -> Decimal:
    if float_shares is None:
        return Decimal("0.25")
    if float_shares <= Decimal("5000000"):
        return ONE
    if float_shares <= Decimal("10000000"):
        return Decimal("0.85")
    if float_shares <= Decimal("20000000"):
        return Decimal("0.65")
    if float_shares <= Decimal("50000000"):
        return Decimal("0.30")
    return Decimal("0.05")


def catalyst_score(state: CatalystStatus) -> Decimal:
    return {
        CatalystStatus.TRUE: ONE,
        CatalystStatus.FALSE: Decimal("0.10"),
        CatalystStatus.UNKNOWN: Decimal("0.30"),
        CatalystStatus.UNAVAILABLE: Decimal("0.20"),
    }[state]


def momentum_score(
    *, percentage_change: Decimal | None, relative_volume: Decimal | None,
    acceleration: Decimal | None, float_shares: Decimal | None,
    dollar_volume: Decimal | None, catalyst_state: CatalystStatus,
    setup_quality: Decimal | None, spread_percent: Decimal | None,
    weights: ScoreWeights = ScoreWeights(),
) -> MomentumScore:
    normalized = {
        "percentage_change": price_change_score(percentage_change),
        "relative_volume": relative_volume_score(relative_volume),
        "short_term_acceleration": _linear(acceleration, Decimal("0.75"), Decimal("3")),
        "float_quality": float_score(float_shares),
        "liquidity": _linear(dollar_volume, Decimal("100000"), Decimal("10000000")),
        "catalyst_quality": catalyst_score(catalyst_state),
        "technical_setup_quality": ZERO if setup_quality is None else max(ZERO, min(ONE, setup_quality / 100)),
        "execution_quality": ZERO if spread_percent is None else max(ZERO, min(ONE, ONE - spread_percent / Decimal("2"))),
    }
    components = tuple(
        (name, (normalized[name] * getattr(weights, name)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        for name in weights.__dataclass_fields__
    )
    total = max(ZERO, min(Decimal("100"), sum((value for _, value in components), ZERO)))
    return MomentumScore(total=total, components=components)


__all__ = ["price_change_score", "relative_volume_score", "float_score", "catalyst_score", "momentum_score"]
