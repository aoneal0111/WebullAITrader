from __future__ import annotations

from decimal import Decimal

from app.backtesting.models import HistoricalFrame, canonical_fingerprint


def validate_frames(frames: tuple[HistoricalFrame, ...]) -> None:
    if not frames:
        raise ValueError("historical frames must not be empty")
    symbol = frames[0].candle.symbol.strip().upper()
    previous = None
    for frame in frames:
        candle = frame.candle
        if not symbol or candle.symbol.strip().upper() != symbol:
            raise ValueError("historical symbols must be non-empty and identical")
        if candle.open_timestamp.tzinfo is None or candle.close_timestamp.tzinfo is None:
            raise ValueError("candle timestamps must be timezone-aware")
        if candle.open_timestamp >= candle.close_timestamp or (previous and candle.open_timestamp <= previous):
            raise ValueError("candle timestamps must be strictly ordered and non-overlapping")
        previous = candle.close_timestamp
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume,
                  frame.execution_bid, frame.execution_ask, frame.execution_last)
        if not all(isinstance(value, Decimal) and value.is_finite() for value in values):
            raise ValueError("OHLCV and execution values must be finite Decimals")
        if min(candle.open, candle.high, candle.low, candle.close) <= 0 or candle.volume < 0:
            raise ValueError("prices must be positive and volume non-negative")
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
            raise ValueError("OHLC values are inconsistent")
        if min(frame.execution_bid, frame.execution_ask, frame.execution_last) <= 0:
            raise ValueError("execution prices must be positive")
        if frame.market_state.symbol.strip().upper() != symbol:
            raise ValueError("market-state symbol mismatch")


def frames_fingerprint(frames: tuple[HistoricalFrame, ...]) -> str:
    return canonical_fingerprint(frames)
