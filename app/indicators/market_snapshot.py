from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from app.indicators.atr import atr
from app.indicators.bollinger import bollinger_bands
from app.indicators.ema import ema
from app.indicators.macd import macd
from app.indicators.rsi import rsi
from app.indicators.vwap import vwap


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    close: Decimal
    ema_12: Decimal
    ema_26: Decimal
    rsi_14: Decimal | None
    macd: Decimal
    macd_signal: Decimal
    macd_histogram: Decimal
    atr_14: Decimal | None
    bollinger_upper: Decimal | None
    bollinger_middle: Decimal | None
    bollinger_lower: Decimal | None
    vwap: Decimal | None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if name == "symbol":
                continue
            value = getattr(self, name)
            if value is not None and not isinstance(value, Decimal):
                object.__setattr__(self, name, Decimal(str(value)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_market_snapshot(
    symbol: str,
    highs: Sequence[Decimal | int | float],
    lows: Sequence[Decimal | int | float],
    closes: Sequence[Decimal | int | float],
    volumes: Sequence[Decimal | int | float],
) -> MarketSnapshot:
    if not symbol.strip():
        raise ValueError("symbol must not be empty")
    if not closes:
        raise ValueError("at least one market bar is required")
    if len({len(highs), len(lows), len(closes), len(volumes)}) != 1:
        raise ValueError("market series must have equal lengths")
    macd_result = macd(closes)
    bands = bollinger_bands(closes)
    return MarketSnapshot(
        symbol=symbol.strip().upper(),
        close=closes[-1] if isinstance(closes[-1], Decimal) else Decimal(str(closes[-1])),
        ema_12=ema(closes, 12)[-1],
        ema_26=ema(closes, 26)[-1],
        rsi_14=rsi(closes)[-1],
        macd=macd_result.macd[-1],
        macd_signal=macd_result.signal[-1],
        macd_histogram=macd_result.histogram[-1],
        atr_14=atr(highs, lows, closes)[-1],
        bollinger_upper=bands.upper[-1],
        bollinger_middle=bands.middle[-1],
        bollinger_lower=bands.lower[-1],
        vwap=vwap(highs, lows, closes, volumes)[-1],
    )
