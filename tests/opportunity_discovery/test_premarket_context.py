from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.opportunity_discovery import DetectionState, default_registry
from app.opportunity_discovery.contracts import FeatureCapabilities
from app.strategies.warrior_momentum.models import MinuteBar
from app.trade_intelligence.taxonomy_paper_bridge import _discovery_context


EASTERN = ZoneInfo("America/New_York")
DAY = datetime(2026, 9, 11, 9, 32, tzinfo=EASTERN)


def _bar(hour, minute, opened, high, low, close, *, session=None, day=DAY.date()):
    timestamp = datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN)
    return MinuteBar(
        "XYZ", timestamp, Decimal(str(opened)), Decimal(str(high)),
        Decimal(str(low)), Decimal(str(close)), Decimal("1000"),
    )


def _value(bars, cutoff=DAY):
    observation = SimpleNamespace(
        symbol="XYZ", timestamp=cutoff, previous_close=Decimal("9"),
        price=Decimal("10.7"), current_volume=Decimal("10000"),
        average_30_day_volume=Decimal("1000"), bid=Decimal("10.69"),
        ask=Decimal("10.70"), float_shares=Decimal("1000000"),
    )
    return SimpleNamespace(
        observation=observation, bars=tuple(bars),
        evaluation_timestamp=cutoff, session="REGULAR",
        quote_provenance="TEST_QUOTE",
    )


def test_premarket_context_survives_regular_transition_and_drives_hod_breakout():
    bars = (
        _bar(8, 0, 10, 10.2, 9.8, 10.0),
        _bar(8, 1, 10, 10.5, 9.9, 10.2),
        _bar(9, 30, 10.3, 10.5, 10.2, 10.4),
        _bar(9, 31, 10.4, 10.8, 10.3, 10.7),
    )
    context, premarket = _discovery_context(_value(bars))
    assert context.capabilities.premarket_history is True
    assert [bar.session for bar in context.completed_bars] == [
        "PREMARKET", "PREMARKET", "REGULAR", "REGULAR",
    ]
    assert premarket["state"] == "PREMARKET_CONTEXT_AVAILABLE"
    assert premarket["bar_count"] == 2
    assert premarket["earliest"] < premarket["latest"] <= DAY
    result = {
        item.strategy_id: item for item in default_registry().evaluate(context)
    }
    assert result["PREMARKET_HIGH_BREAKOUT"].state is DetectionState.DETECTED
    assert result["PREMARKET_HIGH_BREAKOUT"].trigger_level == Decimal("10.5")


def test_premarket_consolidation_uses_only_premarket_bars():
    bars = (
        _bar(8, 0, 9.8, 10.0, 9.8, 9.9),
        _bar(8, 1, 9.9, 10.1, 9.8, 10.0),
        _bar(8, 2, 10.0, 10.05, 9.85, 9.95),
        _bar(8, 3, 9.95, 10.08, 9.9, 10.0),
        _bar(8, 4, 10.0, 10.5, 9.95, 10.4),
    )
    value = _value(bars, datetime(2026, 9, 11, 8, 6, tzinfo=EASTERN))
    context, premarket = _discovery_context(value)
    result = {
        item.strategy_id: item for item in default_registry().evaluate(context)
    }
    assert premarket["bar_count"] == 5
    assert result["PREMARKET_CONSOLIDATION_BREAKOUT"].state is DetectionState.DETECTED
    assert all(bar.session == "PREMARKET" for bar in context.completed_bars)


def test_missing_or_invalid_premarket_context_fails_closed_without_contamination():
    regular = (_bar(9, 30, 10, 10.2, 9.9, 10.1), _bar(9, 31, 10.1, 10.4, 10.0, 10.3))
    wrong_day = _bar(8, 0, 10, 12, 9.8, 11, day=DAY.date() - timedelta(days=1))
    after_hours = _bar(16, 30, 10, 12, 9.8, 11)
    future = _bar(9, 33, 10, 12, 9.8, 11)
    context, premarket = _discovery_context(_value(regular + (wrong_day, after_hours, future)))
    assert context.capabilities.premarket_history is False
    assert premarket["state"] == "PREMARKET_CONTEXT_UNAVAILABLE"
    assert all(bar.session == "REGULAR" for bar in context.completed_bars)
    assert all(bar.completed_at <= DAY for bar in context.completed_bars)
    result = {
        item.strategy_id: item for item in default_registry().evaluate(context)
    }
    assert result["PREMARKET_HIGH_BREAKOUT"].state is DetectionState.UNAVAILABLE
    assert "premarket_history" in result["PREMARKET_HIGH_BREAKOUT"].missing_features


def test_context_contract_still_requires_explicit_premarket_capability():
    assert FeatureCapabilities(premarket_history=False).premarket_history is False
