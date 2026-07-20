from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MarketObservation:
    timestamp: datetime
    symbol: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    session: str | None
    market_status: str | None
    observed_slippage: Decimal | None
    volatility_regime: str | None = None
    trend_regime: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("market observation timestamp must be timezone-aware")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("market observation symbol is required")
        values = (self.open, self.high, self.low, self.close, self.volume, self.bid, self.ask,
                  self.observed_slippage)
        if any(value is not None and (not isinstance(value, Decimal) or not value.is_finite()) for value in values):
            raise ValueError("market observation numeric fields must be finite Decimals")
        prices = (self.open, self.high, self.low, self.close)
        if any(value is not None and value <= 0 for value in prices):
            raise ValueError("OHLC prices must be positive")
        if self.high is not None and any(value is not None and self.high < value for value in (self.open, self.low, self.close)):
            raise ValueError("market observation OHLC values are inconsistent")
        if self.low is not None and any(value is not None and self.low > value for value in (self.open, self.high, self.close)):
            raise ValueError("market observation OHLC values are inconsistent")
        if self.volume is not None and self.volume < 0:
            raise ValueError("market observation volume must be nonnegative")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("market observation bid must not exceed ask")
        if (self.bid is not None and self.bid <= 0) or (self.ask is not None and self.ask <= 0):
            raise ValueError("market observation quotes must be positive")
        for label in (self.session, self.market_status, self.volatility_regime, self.trend_regime):
            if label is not None and (not isinstance(label, str) or not label.strip()):
                raise ValueError("market observation labels must be nonempty strings")
