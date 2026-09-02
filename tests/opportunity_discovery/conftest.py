from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.opportunity_discovery import CompletedBar, DiscoveryContext, FeatureCapabilities

T0 = datetime(2026, 9, 3, 13, 30, tzinfo=UTC)


def bar(minute, open_, high, low, close, volume=1000, session="REGULAR", symbol="ABCD"):
    return CompletedBar(symbol, T0 + timedelta(minutes=minute), *(Decimal(str(x)) for x in (open_, high, low, close, volume)), session)


def context(bars, *, cutoff=None, symbol="ABCD", session="REGULAR", capabilities=None, **kwargs):
    return DiscoveryContext(symbol, date(2026, 9, 3), session,
        cutoff or bars[-1].completed_at, tuple(bars), capabilities or FeatureCapabilities(), **kwargs)


def clean_pullback():
    return (
        bar(0, 10, 10.4, 9.95, 10.35, 2000),
        bar(1, 10.35, 10.85, 10.3, 10.8, 2400),
        bar(2, 10.8, 11.05, 10.75, 11, 2600),
        bar(3, 11, 11.0, 10.72, 10.8, 900),
        bar(4, 10.8, 11.15, 10.78, 11.1, 1800),
    )
