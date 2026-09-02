"""Bounded point-in-time technical context for pure research detectors."""

from __future__ import annotations

from decimal import Decimal

from .contracts import DiscoveryContext, Impulse, Pullback, ReferenceLevels

HUNDRED = Decimal("100")


def structural_anchor(context: DiscoveryContext, impulse: Impulse | None) -> str:
    """Strategy-independent 15-minute structural identity.

    Quotes, ranks, scanner scores, and ordinary price changes are excluded.
    The bucket changes only as the market advances to a new structural window.
    """

    timestamp = context.decision_cutoff if impulse is None else impulse.start_time
    minute = timestamp.minute - timestamp.minute % 15
    window = timestamp.replace(minute=minute, second=0, microsecond=0)
    return f"{context.symbol.upper()}|{context.session_date.isoformat()}|{context.session.upper()}|{window.isoformat()}"


def build_impulse(context: DiscoveryContext) -> Impulse | None:
    bars = context.completed_bars
    if len(bars) < 3:
        return None
    # If a green resumption follows red pullback bars, freeze the impulse before
    # that pullback. Otherwise use the latest bounded six-bar expansion.
    end = len(bars) - 1
    if bars[-1].close > bars[-1].open:
        cursor = len(bars) - 2
        while cursor >= 0 and bars[cursor].close < bars[cursor].open:
            cursor -= 1
        if cursor < len(bars) - 2:
            end = cursor
    start = max(0, end - 5)
    window = bars[start:end + 1]
    if len(window) < 2:
        return None
    first, last = window[0], window[-1]
    move = last.close - first.open
    if move <= 0:
        return None
    ranges = [bar.high - bar.low for bar in window]
    older = ranges[0]
    expansion = None if older == 0 else ranges[-1] / older
    hod = max(bar.high for bar in bars)
    volumes = [bar.volume for bar in window]
    return Impulse(
        first.completed_at, last.completed_at, first.open, last.close, move,
        move / first.open * HUNDRED, len(window),
        sum(bar.close > bar.open for bar in window), sum(bar.close < bar.open for bar in window),
        max(volumes), sum(volumes) / Decimal(len(volumes)), expansion,
        (last.close - hod) / hod * HUNDRED,
    )


def build_pullback(context: DiscoveryContext, impulse: Impulse | None) -> Pullback | None:
    if impulse is None or len(context.completed_bars) < 3:
        return None
    bars = context.completed_bars
    end = len(bars) - 1
    cursor = end - 1 if bars[-1].close >= bars[-1].open else end
    pullback = []
    while cursor >= 0 and bars[cursor].completed_at > impulse.end_time and bars[cursor].close <= bars[cursor].open:
        pullback.append(bars[cursor])
        cursor -= 1
    pullback.reverse()
    if not pullback:
        return None
    high = max(impulse.end_price, max(bar.high for bar in pullback))
    low = min(bar.low for bar in pullback)
    depth = high - low
    impulse_move = impulse.absolute_move
    current = bars[-1]
    hod = max(bar.high for bar in bars)
    pull_ranges = [bar.high - bar.low for bar in pullback]
    impulse_ranges = [bar.high - bar.low for bar in bars if impulse.start_time <= bar.completed_at <= impulse.end_time]
    return Pullback(
        pullback[0].completed_at, pullback[-1].completed_at, len(pullback), depth,
        depth / high * HUNDRED,
        None if impulse_move == 0 else depth / impulse_move,
        sum(bar.close < bar.open for bar in pullback), sum(bar.close > bar.open for bar in pullback),
        low, low > impulse.start_price,
        None if impulse.average_volume == 0 else sum(bar.volume for bar in pullback) / Decimal(len(pullback)) / impulse.average_volume,
        None if not impulse_ranges or sum(impulse_ranges) == 0 else
            (sum(pull_ranges) / Decimal(len(pull_ranges))) / (sum(impulse_ranges) / Decimal(len(impulse_ranges))),
        (current.close - impulse.end_price) / impulse.end_price * HUNDRED,
        (current.close - hod) / hod * HUNDRED,
        low <= impulse.start_price,
    )


def build_reference_levels(context: DiscoveryContext, impulse: Impulse | None) -> ReferenceLevels:
    bars = context.completed_bars
    premarket = [bar for bar in bars if bar.session.upper() == "PREMARKET"]
    regular = [bar for bar in bars if bar.session.upper() == "REGULAR"]
    opening = regular[:5] if len(regular) >= 5 else []
    prior = bars[:-1]
    recent = prior[-6:]
    consolidation = prior[-4:]
    return ReferenceLevels(
        max((bar.high for bar in bars), default=None),
        max((bar.high for bar in premarket), default=None),
        max((bar.high for bar in opening), default=None),
        min((bar.low for bar in opening), default=None),
        max((bar.high for bar in recent), default=None),
        min((bar.low for bar in recent), default=None),
        None if impulse is None else max(bar.high for bar in bars if impulse.start_time <= bar.completed_at <= impulse.end_time),
        None if impulse is None else min(bar.low for bar in bars if impulse.start_time <= bar.completed_at <= impulse.end_time),
        max((bar.high for bar in consolidation), default=None),
        min((bar.low for bar in consolidation), default=None),
        max((bar.high for bar in prior[:-1]), default=None) if len(prior) >= 2 else None,
        context.vwap if context.capabilities.authoritative_vwap else None,
        context.prior_close if context.capabilities.prior_close else None,
    )
