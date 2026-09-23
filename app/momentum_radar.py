"""Bounded, non-authoritative early-momentum promotion radar."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping


ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class RadarConfig:
    capacity: int = 500
    snapshot_window: int = 8
    minimum_displacement_for_replacement: Decimal = Decimal("0.10")
    demotion_fraction: Decimal = Decimal("0.75")


@dataclass(frozen=True, slots=True)
class RadarSnapshot:
    symbol: str
    observed_at: datetime
    price: Decimal | None
    change_percent: Decimal | None
    volume: Decimal | None
    relative_volume: Decimal | None
    turnover: Decimal | None
    sources: tuple[str, ...] = ()
    rank: int | None = None
    hod: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RadarAssessment:
    symbol: str
    score: Decimal
    state: str
    components: Mapping[str, Decimal]
    observed_at: datetime


class MomentumRadar:
    """Snapshot radar used only to order bounded observation promotion."""

    def __init__(self, config: RadarConfig = RadarConfig()) -> None:
        if config.capacity <= 0 or config.snapshot_window <= 1:
            raise ValueError("radar bounds must be positive")
        self.config = config
        self._history: OrderedDict[str, deque[RadarSnapshot]] = OrderedDict()
        self._assessments: dict[str, RadarAssessment] = {}
        self._promoted: set[str] = set()
        self._required: set[str] = set()
        self._metrics = {"evaluated": 0, "emerging": 0, "promotions": 0,
                         "demotions": 0, "replacements": 0}

    def observe(self, snapshots: tuple[RadarSnapshot, ...], *, required: tuple[str, ...] = ()) -> tuple[RadarAssessment, ...]:
        self._required = {symbol.strip().upper() for symbol in required if symbol.strip()}
        for snapshot in snapshots:
            symbol = snapshot.symbol.strip().upper()
            if not symbol:
                continue
            history = self._history.setdefault(symbol, deque(maxlen=self.config.snapshot_window))
            history.append(snapshot)
            self._history.move_to_end(symbol)
            while len(self._history) > self.config.capacity:
                old, _ = self._history.popitem(last=False)
                self._assessments.pop(old, None)
                self._promoted.discard(old)
            self._assessments[symbol] = self._assess(symbol, history)
        self._metrics["evaluated"] += len(snapshots)
        self._metrics["emerging"] = sum(
            assessment.state == "MOMENTUM_EMERGING" for assessment in self._assessments.values()
        )
        return tuple(sorted(self._assessments.values(), key=lambda item: (-item.score, item.symbol)))

    def promote(self, *, capacity: int, required: tuple[str, ...] = ()) -> tuple[str, ...]:
        if capacity <= 0:
            return ()
        required_set = self._required | {symbol.strip().upper() for symbol in required if symbol.strip()}
        ordered = sorted(self._assessments.values(), key=lambda item: (-item.score, item.symbol))
        selected: list[str] = []
        for symbol in sorted(required_set):
            if symbol not in selected:
                selected.append(symbol)
        top_score = ordered[0].score if ordered else ZERO
        incumbent_floor = top_score * self.config.demotion_fraction
        for assessment in ordered:
            if assessment.symbol in self._promoted and assessment.score >= incumbent_floor:
                if len(selected) < capacity and assessment.symbol not in selected:
                    selected.append(assessment.symbol)
        for assessment in ordered:
            if len(selected) >= capacity:
                break
            if assessment.symbol not in selected:
                selected.append(assessment.symbol)
        prior = set(self._promoted)
        self._promoted = set(selected)
        self._metrics["promotions"] += len(self._promoted - prior)
        self._metrics["demotions"] += len(prior - self._promoted)
        self._metrics["replacements"] += min(len(prior - self._promoted), len(self._promoted - prior))
        return tuple(selected)

    def priority_order(self) -> tuple[str, ...]:
        return tuple(assessment.symbol for assessment in sorted(
            self._assessments.values(), key=lambda item: (-item.score, item.symbol)
        ))

    def promoted_symbols(self) -> tuple[str, ...]:
        """Return the current bounded promotion set for transition telemetry."""
        return tuple(sorted(self._promoted))

    def assessment(self, symbol: str) -> RadarAssessment | None:
        return self._assessments.get(symbol.strip().upper())

    def metrics(self) -> dict[str, int]:
        return dict(self._metrics)

    def state_snapshot(self) -> tuple[RadarAssessment, ...]:
        return tuple(sorted(self._assessments.values(), key=lambda item: item.symbol))

    def _assess(self, symbol: str, history: deque[RadarSnapshot]) -> RadarAssessment:
        current = history[-1]
        previous = history[-2] if len(history) > 1 else None
        price_accel = _delta_percent(current.price, None if previous is None else previous.price)
        volume_rate = _delta(current.volume, None if previous is None else previous.volume)
        rvol_accel = _delta(current.relative_volume, None if previous is None else previous.relative_volume)
        turnover_accel = _delta(current.turnover, None if previous is None else previous.turnover)
        hod_pressure = _hod_pressure(current)
        source_diversity = min(Decimal("1"), Decimal(len(set(current.sources))) / Decimal("4"))
        components = {
            "price_acceleration": _clamp(price_accel / Decimal("5"), ZERO, Decimal("1")),
            "volume_acceleration": _clamp(volume_rate / Decimal("2"), ZERO, Decimal("1")),
            "rvol_acceleration": _clamp(rvol_accel / Decimal("2"), ZERO, Decimal("1")),
            "turnover_acceleration": _clamp(turnover_accel / Decimal("2"), ZERO, Decimal("1")),
            "hod_pressure": hod_pressure,
            "source_diversity": source_diversity,
            "established_importance": _clamp((current.change_percent or ZERO) / Decimal("50"), ZERO, Decimal("1")),
        }
        score = (
            components["price_acceleration"] * Decimal("35")
            + components["volume_acceleration"] * Decimal("15")
            + components["rvol_acceleration"] * Decimal("15")
            + components["turnover_acceleration"] * Decimal("10")
            + components["hod_pressure"] * Decimal("10")
            + components["source_diversity"] * Decimal("10")
            + components["established_importance"] * Decimal("5")
        )
        state = "MOMENTUM_EMERGING" if (
            components["price_acceleration"] > ZERO
            and (components["volume_acceleration"] > ZERO or components["rvol_acceleration"] > ZERO)
        ) else "RADAR_MONITORING"
        return RadarAssessment(symbol, score, state, components, current.observed_at)


def _delta(current: Decimal | None, previous: Decimal | None) -> Decimal:
    if current is None or previous is None or previous <= ZERO:
        return ZERO
    return max(ZERO, current / previous - Decimal("1"))


def _delta_percent(current: Decimal | None, previous: Decimal | None) -> Decimal:
    if current is None or previous is None or previous <= ZERO:
        return ZERO
    return max(ZERO, (current - previous) / previous * HUNDRED)


def _hod_pressure(snapshot: RadarSnapshot) -> Decimal:
    if snapshot.price is None or snapshot.hod is None or snapshot.hod <= ZERO:
        return ZERO
    return _clamp(snapshot.price / snapshot.hod, ZERO, Decimal("1"))


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))
