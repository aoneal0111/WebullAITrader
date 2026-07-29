from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from random import Random

from app.gui.models import Candle, CandleInterval, CandleSeriesSnapshot


@dataclass(frozen=True, slots=True)
class DemoMarketProvider:
    """Produce deterministic OHLCV snapshots for offline Atlas development."""

    seed: int = 20260728
    candle_count: int = 180
    starting_price: Decimal = Decimal("185.00")
    symbol: str = "ATLAS"
    venue: str = "DEMO"

    def snapshot(
        self,
        symbol: str | None = None,
        interval: CandleInterval = CandleInterval.ONE_MINUTE,
    ) -> CandleSeriesSnapshot:
        rng = Random(self.seed)
        interval_minutes = self._interval_minutes(interval)
        end_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start_time = end_time - timedelta(
            minutes=interval_minutes * max(self.candle_count - 1, 0)
        )

        candles: list[Candle] = []
        previous_close = self.starting_price

        for index in range(self.candle_count):
            regime = (index // 30) % 6
            drift = self._regime_drift(regime)
            volatility = self._regime_volatility(regime)
            shock = Decimal(str(rng.uniform(-1.0, 1.0))) * volatility

            open_price = previous_close
            close_price = max(
                Decimal("1.00"),
                open_price + drift + shock,
            )
            wick = Decimal(str(rng.uniform(0.10, 0.85))) * volatility
            high_price = max(open_price, close_price) + wick
            low_price = max(
                Decimal("0.01"),
                min(open_price, close_price) - wick,
            )
            volume = Decimal(
                str(
                    int(
                        120_000
                        + rng.uniform(0, 180_000)
                        + abs(float(shock)) * 90_000
                    )
                )
            )

            candles.append(
                Candle(
                    timestamp=start_time
                    + timedelta(minutes=index * interval_minutes),
                    open=self._price(open_price),
                    high=self._price(high_price),
                    low=self._price(low_price),
                    close=self._price(close_price),
                    volume=volume,
                )
            )
            previous_close = close_price

        return CandleSeriesSnapshot(
            symbol=symbol or self.symbol,
            interval=interval,
            candles=tuple(candles),
            venue=self.venue,
        )

    @staticmethod
    def _interval_minutes(interval: CandleInterval) -> int:
        return {
            CandleInterval.ONE_MINUTE: 1,
            CandleInterval.FIVE_MINUTES: 5,
            CandleInterval.FIFTEEN_MINUTES: 15,
        }[interval]

    @staticmethod
    def _regime_drift(regime: int) -> Decimal:
        return (
            Decimal("0.08"),
            Decimal("0.02"),
            Decimal("-0.09"),
            Decimal("0.00"),
            Decimal("0.13"),
            Decimal("-0.03"),
        )[regime]

    @staticmethod
    def _regime_volatility(regime: int) -> Decimal:
        return (
            Decimal("0.55"),
            Decimal("0.30"),
            Decimal("0.75"),
            Decimal("0.22"),
            Decimal("0.95"),
            Decimal("0.42"),
        )[regime]

    @staticmethod
    def _price(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))
