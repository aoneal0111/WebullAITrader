from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.trade_intelligence.knowledge.features import (
    FEATURE_DERIVATION_VERSION, derive_point_in_time_features, prefix_invariant_features,
)
from app.trade_intelligence.knowledge.models import HistoricalBar


def _bars():
    base = datetime(2026, 8, 7, 13, 30, tzinfo=UTC)
    return tuple(HistoricalBar("ABC", base + timedelta(minutes=i), Decimal(str(10+i/10)),
                               Decimal(str(10.2+i/10)), Decimal(str(9.9+i/10)), Decimal(str(10.1+i/10)),
                               Decimal(100 + i), "REGULAR", "ALPACA", "IEX", "UTC", 1,
                               trade_count=5+i, provider_vwap=Decimal("10.05")) for i in range(12))


def test_features_are_versioned_point_in_time_and_exclude_outcomes():
    bars = _bars(); value = derive_point_in_time_features(bars, bars[5].timestamp + timedelta(minutes=1))
    assert value["feature_derivation_version"] == FEATURE_DERIVATION_VERSION
    assert value["bars_used"] == 6
    assert value["momentum"]["return_1bar"] is not None
    assert "mfe" not in value and "target_hit" not in value
    assert value["opening"]["minutes_from_regular_open"] == 5


def test_prefix_invariance_ignores_future_suffix():
    bars = _bars(); cutoff = bars[5].timestamp + timedelta(minutes=1)
    winner = tuple(HistoricalBar(b.symbol, b.timestamp, b.open, b.high + 100, b.low, b.close + 100,
                                 b.volume, b.session, b.provider, b.feed, b.source_timezone, b.normalization_version,
                                 b.previous_close, b.bid, b.ask, b.bid_size, b.ask_size, b.float_shares,
                                 b.market_cap, b.halt_state, b.catalyst_id, b.trade_count, b.provider_vwap) for b in bars[6:])
    assert prefix_invariant_features(bars[:6], winner, cutoff)


def test_premarket_and_vwap_use_only_eligible_bars():
    bars = list(_bars()); pre = HistoricalBar("ABC", datetime(2026, 8, 7, 12, 0, tzinfo=UTC), Decimal("9"), Decimal("9.2"), Decimal("8.8"), Decimal("9.1"), Decimal("50"), "PREMARKET", "ALPACA", "IEX")
    value = derive_point_in_time_features((pre, *bars), bars[5].timestamp + timedelta(minutes=1), previous_close=Decimal("8"))
    assert value["premarket"]["bar_count"] == 1
    assert value["levels"]["vwap"] is not None
    assert value["data_quality"]["feeds"] == ["IEX"]
