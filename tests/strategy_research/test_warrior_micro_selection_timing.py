"""Offline Warrior setup-selection timing: observations are not orders."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.strategy_research.warrior_micro_selection_timing import (
    review_micro_selection,
)
from tests.warrior_momentum.test_strategy import micro_bars


def test_early_micro_trigger_is_visible_below_prior_hod():
    bars = list(micro_bars())
    bars[-1] = replace(bars[-1], high=D("10.58"), close=D("10.57"))
    rows = review_micro_selection(tuple(bars))
    assert len(rows) == 1
    row = rows[0]
    assert row.at == bars[-1].timestamp
    assert row.selected_type == "MICRO_PULLBACK"
    assert row.trigger_premium == D("0")
    assert row.micro_trigger < row.micro_close < max(
        b.high for b in bars[:-1]
    )


def test_simultaneous_hod_priority_records_trigger_premium_not_fill():
    bars = list(micro_bars())
    bars[-2] = replace(bars[-2], close=D("10.50"))
    rows = review_micro_selection(tuple(bars))
    assert len(rows) == 1
    row = rows[0]
    assert row.selected_type == "HIGH_OF_DAY_BREAKOUT"
    assert row.selected_trigger > row.micro_trigger
    assert row.trigger_premium == row.selected_trigger - row.micro_trigger


def test_incomplete_intrabar_touch_does_not_create_executable_trigger():
    bars = list(micro_bars())
    bars[-1] = replace(bars[-1], high=D("10.58"), close=D("10.54"))
    assert review_micro_selection(tuple(bars)) == ()


def test_prefix_only_prevents_future_data_from_rewriting_prior_decision():
    bars = tuple(micro_bars())
    before = review_micro_selection(bars)
    later = replace(
        bars[-1], timestamp=bars[-1].timestamp.replace(minute=bars[-1].timestamp.minute + 1),
        close=D("11"), high=D("11.1"),
    )
    after = review_micro_selection(bars + (later,))
    assert after[:len(before)] == before


def test_mixed_symbols_or_reordered_timestamps_are_rejected():
    bars = tuple(micro_bars())
    with pytest.raises(ValueError, match="SINGLE_SYMBOL_STRICT_TIME_REQUIRED"):
        review_micro_selection(bars[:-1] + (replace(bars[-1], symbol="OTHER"),))
    with pytest.raises(ValueError, match="SINGLE_SYMBOL_STRICT_TIME_REQUIRED"):
        review_micro_selection(bars[:-1] + (replace(bars[-1], timestamp=bars[-2].timestamp),))
