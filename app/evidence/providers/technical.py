from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable

from app.evidence.enums import EvidenceCategory, SignalDirection
from app.evidence.exceptions import EvidenceValidationError
from app.evidence.models import Evidence, JSONValue
from app.indicators.market_snapshot import MarketSnapshot


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class TechnicalSnapshotInput:
    """A market snapshot paired with its deterministic observation time."""

    snapshot: MarketSnapshot
    timestamp: datetime


class TechnicalSnapshotEvidenceProvider:
    """Convert canonical snapshot indicators into deterministic evidence."""

    name = "technical_snapshot_v1"

    def generate(
        self,
        snapshot_input: TechnicalSnapshotInput,
    ) -> tuple[Evidence, ...]:
        if not isinstance(snapshot_input, TechnicalSnapshotInput):
            raise EvidenceValidationError(
                "input must be a TechnicalSnapshotInput"
            )
        if not isinstance(snapshot_input.snapshot, MarketSnapshot):
            raise EvidenceValidationError(
                "snapshot must be a MarketSnapshot"
            )
        timestamp = snapshot_input.timestamp
        if not isinstance(timestamp, datetime):
            raise EvidenceValidationError("timestamp must be a datetime")
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise EvidenceValidationError(
                "timestamp must be timezone-aware"
            )

        snapshot = snapshot_input.snapshot
        if not isinstance(snapshot.symbol, str) or not snapshot.symbol.strip():
            raise EvidenceValidationError("snapshot symbol cannot be empty")

        numeric_names = (
            "close",
            "ema_12",
            "ema_26",
            "rsi_14",
            "macd",
            "macd_signal",
            "macd_histogram",
            "atr_14",
            "bollinger_upper",
            "bollinger_middle",
            "bollinger_lower",
            "vwap",
        )
        for name in numeric_names:
            value = getattr(snapshot, name)
            if value is not None and not value.is_finite():
                raise EvidenceValidationError(f"{name} must be finite")

        if snapshot.close <= ZERO:
            raise EvidenceValidationError("close must be positive")
        if snapshot.rsi_14 is not None and not (
            ZERO <= snapshot.rsi_14 <= Decimal("100")
        ):
            raise EvidenceValidationError("rsi_14 must be between 0 and 100")
        if snapshot.atr_14 is not None and snapshot.atr_14 < ZERO:
            raise EvidenceValidationError("atr_14 cannot be negative")

        bands = (
            snapshot.bollinger_lower,
            snapshot.bollinger_middle,
            snapshot.bollinger_upper,
        )
        if all(value is not None for value in bands) and not (
            bands[0] <= bands[1] <= bands[2]  # type: ignore[operator]
        ):
            raise EvidenceValidationError(
                "Bollinger bands must satisfy lower <= middle <= upper"
            )

        builders: tuple[Callable[[TechnicalSnapshotInput], Evidence | None], ...] = (
            self._ema,
            self._macd,
            self._rsi,
            self._vwap,
            self._bollinger,
            self._atr,
        )
        return tuple(
            item
            for builder in builders
            if (item := builder(snapshot_input)) is not None
        )

    def _evidence(
        self,
        snapshot_input: TechnicalSnapshotInput,
        *,
        indicator: str,
        direction: SignalDirection,
        strength: Decimal,
        confidence: Decimal,
        explanation: str,
        features: dict[str, JSONValue],
        metadata: dict[str, JSONValue] | None = None,
    ) -> Evidence:
        item_metadata: dict[str, JSONValue] = {
            "provider_version": "1",
            "indicator": indicator,
            "deterministic": True,
        }
        if metadata:
            item_metadata.update(metadata)
        return Evidence(
            symbol=snapshot_input.snapshot.symbol,
            timestamp=snapshot_input.timestamp,
            source=self.name,
            category=EvidenceCategory.TECHNICAL,
            direction=direction,
            confidence=float(confidence),
            strength=float(strength),
            explanation=explanation,
            features=features,
            metadata=item_metadata,
        )

    def _ema(self, value: TechnicalSnapshotInput) -> Evidence:
        snapshot = value.snapshot
        direction = _direction(snapshot.ema_12 - snapshot.ema_26)
        distance = abs(snapshot.ema_12 - snapshot.ema_26) / snapshot.close
        strength = _clamp(distance * Decimal("20"))
        confidence = min(Decimal("0.90"), Decimal("0.50") + strength * Decimal("0.40"))
        relationship = _relationship(direction, "above", "below", "equal to")
        return self._evidence(
            value,
            indicator="ema_cross",
            direction=direction,
            strength=strength,
            confidence=confidence,
            explanation=f"EMA cross: EMA 12 is {relationship} EMA 26.",
            features={
                "close": str(snapshot.close),
                "ema_12": str(snapshot.ema_12),
                "ema_26": str(snapshot.ema_26),
                "normalized_distance": str(distance),
            },
        )

    def _macd(self, value: TechnicalSnapshotInput) -> Evidence:
        snapshot = value.snapshot
        direction = _direction(snapshot.macd_histogram)
        magnitude = abs(snapshot.macd_histogram) / snapshot.close
        strength = _clamp(magnitude * Decimal("100"))
        confidence = min(Decimal("0.90"), Decimal("0.50") + strength * Decimal("0.40"))
        relationship = _relationship(direction, "positive", "negative", "zero")
        return self._evidence(
            value,
            indicator="macd",
            direction=direction,
            strength=strength,
            confidence=confidence,
            explanation=f"MACD: the histogram is {relationship} relative to zero.",
            features={
                "macd": str(snapshot.macd),
                "macd_signal": str(snapshot.macd_signal),
                "macd_histogram": str(snapshot.macd_histogram),
                "normalized_magnitude": str(magnitude),
            },
        )

    def _rsi(self, value: TechnicalSnapshotInput) -> Evidence | None:
        rsi = value.snapshot.rsi_14
        if rsi is None:
            return None
        if rsi <= Decimal("30"):
            direction = SignalDirection.LONG
            strength = _clamp((Decimal("30") - rsi) / Decimal("30"))
            confidence = min(Decimal("0.90"), Decimal("0.55") + strength * Decimal("0.35"))
            condition = "an oversold condition at or below 30"
        elif rsi >= Decimal("70"):
            direction = SignalDirection.SHORT
            strength = _clamp((rsi - Decimal("70")) / Decimal("30"))
            confidence = min(Decimal("0.90"), Decimal("0.55") + strength * Decimal("0.35"))
            condition = "an overbought condition at or above 70"
        else:
            direction = SignalDirection.NEUTRAL
            strength = _clamp(abs(rsi - Decimal("50")) / Decimal("20"))
            confidence = min(Decimal("0.70"), Decimal("0.50") + (ONE - strength) * Decimal("0.20"))
            condition = "a midrange overbought/oversold reading between 30 and 70"
        return self._evidence(
            value,
            indicator="rsi_14",
            direction=direction,
            strength=strength,
            confidence=confidence,
            explanation=f"RSI 14: the observed value indicates {condition}.",
            features={
                "rsi_14": str(rsi),
                "lower_threshold": "30",
                "upper_threshold": "70",
            },
        )

    def _vwap(self, value: TechnicalSnapshotInput) -> Evidence | None:
        snapshot = value.snapshot
        if snapshot.vwap is None:
            return None
        direction = _direction(snapshot.close - snapshot.vwap)
        distance = abs(snapshot.close - snapshot.vwap) / snapshot.close
        strength = _clamp(distance * Decimal("20"))
        confidence = min(Decimal("0.85"), Decimal("0.50") + strength * Decimal("0.35"))
        relationship = _relationship(direction, "above", "below", "equal to")
        return self._evidence(
            value,
            indicator="vwap",
            direction=direction,
            strength=strength,
            confidence=confidence,
            explanation=f"VWAP: the close is {relationship} the supplied VWAP.",
            features={
                "close": str(snapshot.close),
                "vwap": str(snapshot.vwap),
                "normalized_distance": str(distance),
            },
        )

    def _bollinger(self, value: TechnicalSnapshotInput) -> Evidence | None:
        snapshot = value.snapshot
        upper = snapshot.bollinger_upper
        middle = snapshot.bollinger_middle
        lower = snapshot.bollinger_lower
        if upper is None or middle is None or lower is None:
            return None
        width = upper - lower
        if width == ZERO:
            direction = SignalDirection.NEUTRAL
            strength = ZERO
            confidence = Decimal("0.50")
            location = "the bands have zero width"
        else:
            half_width = width / Decimal("2")
            if snapshot.close <= lower:
                direction = SignalDirection.LONG
                strength = _clamp((middle - snapshot.close) / half_width)
                confidence = min(Decimal("0.85"), Decimal("0.50") + strength * Decimal("0.35"))
                location = "at or below the lower band"
            elif snapshot.close >= upper:
                direction = SignalDirection.SHORT
                strength = _clamp((snapshot.close - middle) / half_width)
                confidence = min(Decimal("0.85"), Decimal("0.50") + strength * Decimal("0.35"))
                location = "at or above the upper band"
            else:
                direction = SignalDirection.NEUTRAL
                strength = _clamp(abs(snapshot.close - middle) / half_width)
                confidence = min(Decimal("0.70"), Decimal("0.50") + (ONE - strength) * Decimal("0.20"))
                location = "inside the outer bands"
        return self._evidence(
            value,
            indicator="bollinger_bands",
            direction=direction,
            strength=strength,
            confidence=confidence,
            explanation=f"Bollinger Bands: the observed close is {location}; this describes location, not a guaranteed reversal.",
            features={
                "close": str(snapshot.close),
                "upper": str(upper),
                "middle": str(middle),
                "lower": str(lower),
                "band_width": str(width),
            },
        )

    def _atr(self, value: TechnicalSnapshotInput) -> Evidence | None:
        snapshot = value.snapshot
        if snapshot.atr_14 is None:
            return None
        atr_percent = snapshot.atr_14 / snapshot.close
        strength = _clamp(atr_percent * Decimal("20"))
        confidence = min(Decimal("0.80"), Decimal("0.50") + strength * Decimal("0.30"))
        regime = "low" if atr_percent < Decimal("0.01") else "moderate" if atr_percent < Decimal("0.03") else "high"
        return self._evidence(
            value,
            indicator="atr_14",
            direction=SignalDirection.NEUTRAL,
            strength=strength,
            confidence=confidence,
            explanation=f"ATR 14: ATR is {atr_percent} of the close, indicating {regime} volatility.",
            features={
                "atr_14": str(snapshot.atr_14),
                "close": str(snapshot.close),
                "atr_percent": str(atr_percent),
            },
            metadata={"role": "volatility_context"},
        )


def _clamp(value: Decimal) -> Decimal:
    return max(ZERO, min(ONE, value))


def _direction(value: Decimal) -> SignalDirection:
    if value > ZERO:
        return SignalDirection.LONG
    if value < ZERO:
        return SignalDirection.SHORT
    return SignalDirection.NEUTRAL


def _relationship(
    direction: SignalDirection,
    bullish: str,
    bearish: str,
    neutral: str,
) -> str:
    if direction is SignalDirection.LONG:
        return bullish
    if direction is SignalDirection.SHORT:
        return bearish
    return neutral
