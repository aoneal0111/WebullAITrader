from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.dynamic_momentum_discovery import (
    BroadMarketSnapshot,
    DiscoverySource,
    SourceMembership,
)


NOW = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)
D = Decimal


def snapshot(symbol="MOMO", **changes):
    values = dict(
        symbol=symbol,
        decision_cutoff=NOW,
        session="REGULAR",
        memberships=(
            SourceMembership(DiscoverySource.SESSION_GAINERS, 60, 2),
            SourceMembership(DiscoverySource.RELATIVE_VOLUME_10D, 44, 1),
        ),
        price=D("10"), previous_close=D("8"), open_price=D("9"),
        session_high=D("10"),
        prior_session_high=D("9.90"), volume=D("500000"),
        relative_volume=D("4"), turnover=D("5000000"),
        bid=D("9.98"), ask=D("10.00"), bid_size=D("800"), ask_size=D("700"),
        quote_timestamp=NOW - timedelta(milliseconds=100),
        recent_1m_change_percent=D("3"), recent_5m_change_percent=D("7"),
        volume_acceleration=D("2"), fresh_high_count=3,
        first_acceleration_at=NOW - timedelta(minutes=4),
        production_stages=(),
    )
    values.update(changes)
    return BroadMarketSnapshot(**values)
