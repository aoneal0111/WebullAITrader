"""Broad discovery and deterministic stocks-in-play detectors."""

from __future__ import annotations

from decimal import Decimal

from app.momentum_scanner.models import ScannerMetrics, ScannerObservation

from .configuration import DiscoveryConfig
from .features import build_features, rolling_change
from .models import CandidateStatus, MinuteBar, ReasonCode, StockInPlayType


def discovery_reasons(observation: ScannerObservation, metrics: ScannerMetrics, config: DiscoveryConfig) -> tuple[ReasonCode, ...]:
    reasons: list[ReasonCode] = []
    if observation.price < config.minimum_price:
        reasons.append(ReasonCode.PRICE_TOO_LOW)
    if observation.price > config.maximum_price:
        reasons.append(ReasonCode.PRICE_TOO_HIGH)
    if metrics.percentage_change < config.minimum_percentage_change:
        reasons.append(ReasonCode.CHANGE_TOO_LOW)
    if metrics.relative_volume < config.minimum_relative_volume:
        reasons.append(ReasonCode.RVOL_LOW)
    if observation.float_shares is not None and observation.float_shares > config.maximum_float:
        reasons.append(ReasonCode.FLOAT_HIGH)
    if (
        observation.current_volume < config.minimum_volume
        or metrics.dollar_volume < config.minimum_dollar_volume
    ):
        reasons.append(ReasonCode.LIQUIDITY_LOW)
    if metrics.spread_percent is None or metrics.spread_percent > config.maximum_spread_percent:
        reasons.append(ReasonCode.SPREAD_WIDE)
    if not observation.tradable:
        reasons.append(ReasonCode.NOT_TRADABLE)
    if observation.halted:
        reasons.append(ReasonCode.HALTED)
    return tuple(reasons)


def candidate_status(score: Decimal, reasons: tuple[ReasonCode, ...], config: DiscoveryConfig) -> CandidateStatus:
    if score >= config.qualified_score and discovery_qualified(reasons):
        return CandidateStatus.QUALIFIED
    if score >= config.near_qualified_score:
        return CandidateStatus.NEAR_QUALIFIED
    if score >= config.watch_score:
        return CandidateStatus.WATCH
    return CandidateStatus.DISCOVERED


def discovery_qualified(reasons: tuple[ReasonCode, ...]) -> bool:
    """Catalyst evidence is quality context; every other discovery reason is a gate."""
    return not any(code not in {ReasonCode.NO_CATALYST, ReasonCode.CATALYST_UNKNOWN} for code in reasons)


def detect_stocks_in_play(
    bars: tuple[MinuteBar, ...], *, percentage_change: Decimal,
    relative_volume: Decimal, top_gapper: bool = False,
) -> tuple[StockInPlayType, ...]:
    found: list[StockInPlayType] = []
    if top_gapper:
        found.append(StockInPlayType.TOP_GAPPER)
    if relative_volume >= Decimal("5"):
        found.append(StockInPlayType.HIGH_RELATIVE_VOLUME)
    features = build_features(bars)
    change5 = rolling_change(bars, 5)
    change10 = rolling_change(bars, 10)
    if change5 is not None and change5 >= Decimal("3"):
        found.append(StockInPlayType.RUNNING_UP)
    if features is not None and features.distance_from_hod_percent <= Decimal("1") and percentage_change > 0:
        found.append(StockInPlayType.HIGH_OF_DAY_MOMENTUM)
    if change5 is not None and change5 >= Decimal("5"):
        found.append(StockInPlayType.SQUEEZE_5_IN_5)
    if change10 is not None and change10 >= Decimal("10"):
        found.append(StockInPlayType.SQUEEZE_10_IN_10)
    return tuple(found)


__all__ = ["discovery_reasons", "discovery_qualified", "candidate_status", "detect_stocks_in_play"]
