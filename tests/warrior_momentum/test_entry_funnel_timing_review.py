"""Reproduce legacy timing/selection behavior without changing strategy policy."""
from dataclasses import replace
from decimal import Decimal as D

from app.strategies.warrior_momentum import (
    SetupState, SetupType, detect_best_setup, detect_hod_breakout,
    detect_micro_pullback,
)
from tests.warrior_momentum.test_strategy import micro_bars


def test_micro_pullback_can_trigger_below_prior_hod():
    bars=list(micro_bars())
    bars[-1]=replace(bars[-1],high=D('10.58'),close=D('10.57'))
    prior_hod=max(b.high for b in bars[:-1])
    micro=detect_micro_pullback(tuple(bars))
    selected=detect_best_setup(tuple(bars))
    assert micro.state is SetupState.TRIGGERED
    assert micro.trigger<bars[-1].close<prior_hod
    assert selected.setup_type is SetupType.MICRO_PULLBACK
    assert selected.state is SetupState.TRIGGERED


def test_intrabar_trigger_touch_without_close_is_still_forming():
    bars=list(micro_bars())
    bars[-1]=replace(bars[-1],high=D('10.58'),close=D('10.54'))
    result=detect_micro_pullback(tuple(bars))
    assert bars[-1].high>=result.trigger>bars[-1].close
    assert result.state is SetupState.FORMING


def test_simultaneous_hod_and_micro_triggers_choose_higher_scored_hod():
    bars=list(micro_bars())
    bars[-2]=replace(bars[-2],close=D('10.50'))
    micro=detect_micro_pullback(tuple(bars))
    hod=detect_hod_breakout(tuple(bars))
    selected=detect_best_setup(tuple(bars))
    assert micro.state is hod.state is SetupState.TRIGGERED
    assert hod.score>micro.score
    assert selected.setup_type is SetupType.HIGH_OF_DAY_BREAKOUT
    assert selected.trigger>micro.trigger
