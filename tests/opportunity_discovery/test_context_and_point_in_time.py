from dataclasses import replace
from datetime import timedelta

import pytest

from app.opportunity_discovery import (
    DiscoveryContext, FeatureCapabilities, build_impulse, build_pullback,
    build_reference_levels,
)
from tests.opportunity_discovery.conftest import T0, bar, clean_pullback, context


def test_impulse_and_pullback_use_completed_information_only():
    ctx = context(clean_pullback())
    impulse = build_impulse(ctx)
    pullback = build_pullback(ctx, impulse)
    assert impulse.end_time <= ctx.decision_cutoff
    assert impulse.percentage_move > 0 and impulse.duration_bars == 3
    assert pullback.bars == 1 and pullback.higher_low
    assert pullback.volume_contraction < 1 and not pullback.invalidated


def test_future_bar_hod_volume_and_impulse_endpoint_are_rejected():
    bars = clean_pullback()
    with pytest.raises(ValueError, match="anti-lookahead"):
        context(bars, cutoff=bars[-1].completed_at - timedelta(seconds=1))
    ctx = context(bars[:-1])
    levels = build_reference_levels(ctx, build_impulse(ctx))
    assert levels.current_hod == max(item.high for item in bars[:-1])
    assert bars[-1].high > levels.current_hod


def test_vwap_prior_close_and_unbounded_history_cannot_be_fabricated():
    bars = clean_pullback()
    with pytest.raises(ValueError, match="VWAP"):
        context(bars, vwap=bars[-1].close)
    with pytest.raises(ValueError, match="prior close"):
        context(bars, prior_close=bars[0].open)
    many = tuple(replace(bars[0], completed_at=T0 + timedelta(minutes=i)) for i in range(65))
    with pytest.raises(ValueError, match="bounded"):
        context(many)


def test_premarket_and_opening_range_levels_require_completed_bars():
    bars = tuple(bar(i, 10, 10.2 + i / 100, 9.9, 10.1, session="PREMARKET") for i in range(3)) + tuple(
        bar(3 + i, 10.1, 10.3 + i / 100, 10, 10.2, session="REGULAR") for i in range(5))
    levels = build_reference_levels(context(bars), build_impulse(context(bars)))
    assert levels.premarket_high == max(item.high for item in bars[:3])
    assert levels.opening_range_high == max(item.high for item in bars[3:])
