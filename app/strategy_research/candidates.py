"""Causal long-only hypotheses. Published daily rules and intraday adaptations
are deliberately named separately: published win rates do not transfer here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.indicators.rsi import rsi

D = Decimal


class Candidate(StrEnum):
    RSI2_DAILY = "RSI2_DAILY"
    IMPROVED_R2_DAILY = "IMPROVED_R2_DAILY"
    BOLLINGER_PERCENT_B_DAILY_LIMIT = "BOLLINGER_PERCENT_B_DAILY_LIMIT"
    RSI2_VWAP_PULLBACK = "RSI2_VWAP_PULLBACK"
    VWAP_BAND_REENTRY = "VWAP_BAND_REENTRY"
    FAILED_BREAKDOWN_RECLAIM = "FAILED_BREAKDOWN_RECLAIM"
    BREAKOUT_CONTROL = "BREAKOUT_CONTROL"
    OPENING_RANGE_RETEST = "OPENING_RANGE_RETEST"
    TREND_VWAP_PULLBACK = "TREND_VWAP_PULLBACK"


DAILY = frozenset({Candidate.RSI2_DAILY, Candidate.IMPROVED_R2_DAILY,
                   Candidate.BOLLINGER_PERCENT_B_DAILY_LIMIT})


@dataclass(frozen=True)
class Bar:
    """Open/close timestamps and OHLCV; session_end is supplied by data owner.

    Intraday inputs must be completed regular-session one-minute bars. Explicit
    session ends support early closes without assuming every day ends at 16:00.
    """
    symbol: str
    opened: datetime
    closed: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    session_end: datetime


def validate_bars(bars: tuple[Bar, ...], timeframe: str) -> None:
    if timeframe not in {"daily", "minute"} or not bars:
        raise ValueError("nonempty daily or minute bars required")
    from zoneinfo import ZoneInfo
    eastern = ZoneInfo("America/New_York")
    previous = None
    session_ends = {}
    symbol = bars[0].symbol
    if not symbol or symbol != symbol.strip().upper():
        raise ValueError("canonical symbol required")
    for b in bars:
        if b.symbol != symbol:
            raise ValueError("one symbol per comparison required")
        if any(t.utcoffset() is None for t in (b.opened, b.closed, b.session_end)):
            raise ValueError("aware timestamps required")
        if not b.opened < b.closed <= b.session_end:
            raise ValueError("invalid bar/session timestamp order")
        if previous and b.opened < previous.closed:
            raise ValueError("bars must be ordered without overlap or duplicates")
        if not all(isinstance(x, D) and x.is_finite() for x in
                   (b.open, b.high, b.low, b.close, b.volume)):
            raise ValueError("finite Decimal OHLCV required")
        if min(b.open, b.high, b.low, b.close) <= 0 or b.volume < 0:
            raise ValueError("positive prices and nonnegative volume required")
        if not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high:
            raise ValueError("inconsistent OHLC")
        day = b.opened.astimezone(eastern).date()
        if b.session_end.astimezone(eastern).date() != day:
            raise ValueError("session end must be on bar trading date")
        if session_ends.setdefault(day, b.session_end) != b.session_end:
            raise ValueError("inconsistent session end")
        if timeframe == "minute":
            local = b.opened.astimezone(eastern)
            ending = b.session_end.astimezone(eastern)
            if (b.closed - b.opened != timedelta(minutes=1)
                    or local.hour * 60 + local.minute < 570
                    or local.second or local.microsecond
                    or ending.hour * 60 + ending.minute > 960
                    or ending.second or ending.microsecond):
                raise ValueError("regular-session aligned one-minute bars required")
            if previous and previous.session_end == b.session_end:
                if previous.closed != b.opened:
                    raise ValueError("missing minute bars inside session")
            else:
                if previous and previous.closed != previous.session_end:
                    raise ValueError("incomplete session tail")
                if local.hour * 60 + local.minute != 570:
                    raise ValueError("session must start at 09:30 Eastern")
        elif previous and previous.opened.astimezone(eastern).date() == day:
            raise ValueError("daily input has multiple bars on one trading date")
        previous = b
    if timeframe == "minute" and bars[-1].closed != bars[-1].session_end:
        raise ValueError("incomplete session tail")


@dataclass(frozen=True)
class Signal:
    stop: Decimal
    reason: str
    entry_limit: Decimal | None = None


def percent_b(closes: tuple[Decimal, ...]) -> Decimal | None:
    """Five closes, one population standard deviation on either side."""
    if len(closes) < 5:
        return None
    sample = closes[-5:]
    mean = sum(sample, D(0)) / 5
    deviation = (sum(((p - mean) ** 2 for p in sample), D(0)) / 5).sqrt()
    return (sample[-1] - mean + deviation) / (2 * deviation) if deviation else None


def _vwap(bars: tuple[Bar, ...]) -> tuple[Decimal, Decimal] | None:
    volume = sum((b.volume for b in bars), D(0))
    if volume <= 0:
        return None
    points = tuple(((b.high + b.low + b.close) / 3, b.volume) for b in bars)
    average = sum((p * v for p, v in points), D(0)) / volume
    variance = sum((v * (p - average) ** 2 for p, v in points), D(0)) / volume
    return average, variance.sqrt()


def entry_signal(history: tuple[Bar, ...], candidate: Candidate) -> Signal | None:
    """Only completed history is accepted; no future bar or execution price."""
    warmup = 200 if candidate in DAILY else (8 if candidate is Candidate.OPENING_RANGE_RETEST else 22)
    if len(history) < warmup:
        return None
    closes = tuple(b.close for b in history)
    b = history[-1]
    if candidate in DAILY:
        trend = b.close > sum(closes[-200:]) / 200
        values = rsi(closes, 2)
        if candidate is Candidate.RSI2_DAILY:
            triggered = trend and values[-1] < 10
        elif candidate is Candidate.IMPROVED_R2_DAILY:
            triggered = trend and values[-3] < 65 and values[-3] > values[-2] > values[-1]
        else:
            volumes = tuple(x.volume for x in history[-21:])
            readings = tuple(percent_b(closes[:len(closes)-i]) for i in (2, 1, 0))
            triggered = (trend and min(volumes) >= 50000
                         and sum(volumes) / 21 >= 125000
                         and all(x is not None and x < D('-0.2') for x in readings))
    else:
        # Each session/gap gets fresh feature history; no overnight VWAP or
        # patterns that bridge missing candles.
        if any(x.closed-x.opened != timedelta(minutes=1) for x in history):
            raise ValueError('Intraday candidates require completed one-minute bars')
        if any(a.closed != z.opened or a.session_end != z.session_end
               for a, z in zip(history, history[1:])):
            raise ValueError("intraday signal history must be contiguous within session")
        prior = history[-21:-1]
        previous = history[-2]
        if candidate is Candidate.OPENING_RANGE_RETEST:
            from zoneinfo import ZoneInfo
            opening = history[0].opened.astimezone(ZoneInfo('America/New_York'))
            level = max(x.high for x in history[:5])
            breakout, retest = history[-3:-1]
            triggered = (opening.hour == 9 and opening.minute == 30
                         and history[4].close > history[0].open
                         and breakout.close > level
                         and retest.low <= level <= retest.close
                         and b.close > retest.high and b.close > b.open)
        elif candidate is Candidate.FAILED_BREAKDOWN_RECLAIM:
            level = min(x.low for x in prior)
            triggered = b.low < level < b.close and b.close > b.open
        elif candidate is Candidate.BREAKOUT_CONTROL:
            triggered = previous.close <= max(x.high for x in history[-22:-2]) and b.close > max(x.high for x in prior)
        else:
            current_vwap = _vwap(history)
            prior_vwap = _vwap(history[:-1])
            if current_vwap is None or prior_vwap is None:
                return None
            vw, sd = current_vwap
            pv, ps = prior_vwap
            if candidate is Candidate.RSI2_VWAP_PULLBACK:
                values = rsi(closes, 2)
                triggered = (values[-2] < 10 <= values[-1] and b.close > vw
                             and vw >= pv and b.close > previous.close)
            elif candidate is Candidate.TREND_VWAP_PULLBACK:
                slow = sum(closes[-20:]) / 20
                prior_slow = sum(closes[-21:-1]) / 20
                triggered = (vw >= pv and slow > prior_slow
                             and previous.low <= pv and b.close > vw
                             and b.close > previous.high and b.close > b.open)
            else:
                triggered = (ps > 0 and previous.close < pv - 2 * ps
                             and b.close > vw - 2 * sd and b.close > b.open)
    if not triggered:
        return None
    # Structural stop retained in every variant. This changes published daily
    # systems which had no stop; do not attach their headline rates to ours.
    stop = min(x.low for x in history[-5:]) * D("0.9995")
    limit = b.close * D('0.98') if candidate is Candidate.BOLLINGER_PERCENT_B_DAILY_LIMIT else None
    return Signal(stop, candidate.value, limit) if 0 < stop < (limit or b.close) else None


def exit_signal(history: tuple[Bar, ...], candidate: Candidate) -> bool:
    if candidate is Candidate.BOLLINGER_PERCENT_B_DAILY_LIMIT:
        value = percent_b(tuple(b.close for b in history))
        return value is not None and value > 1
    if candidate is Candidate.RSI2_DAILY:
        return history[-1].close > sum((b.close for b in history[-5:]), D(0)) / 5
    if candidate is Candidate.IMPROVED_R2_DAILY:
        return rsi(tuple(b.close for b in history), 2)[-1] > 75
    return False
