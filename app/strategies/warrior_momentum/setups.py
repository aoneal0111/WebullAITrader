"""Mechanical one-minute setup detectors; no discretionary chart inference."""

from __future__ import annotations

from decimal import Decimal

from .configuration import SetupConfig
from .features import build_features, contiguous_tail
from .models import MinuteBar, ReasonCode, SetupDetection, SetupState, SetupType, StopModel

HUNDRED = Decimal("100")


def _unknown(kind: SetupType) -> SetupDetection:
    return SetupDetection(kind, SetupState.UNKNOWN, Decimal("0"), reason_codes=(ReasonCode.NO_SETUP,))


def detect_hod_breakout(bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig()) -> SetupDetection:
    ordered = contiguous_tail(bars)
    kind = SetupType.HIGH_OF_DAY_BREAKOUT
    if len(ordered) < 5:
        return _unknown(kind)
    latest, prior = ordered[-1], ordered[:-1]
    resistance = max(bar.high for bar in prior)
    near = (resistance - prior[-1].close) / resistance * HUNDRED <= config.hod_proximity_percent
    consolidation_bars = prior[-config.minimum_consolidation_bars:]
    consolidation = (
        max(bar.high for bar in consolidation_bars)
        - min(bar.low for bar in consolidation_bars)
        <= resistance * Decimal("0.03")
    )
    avg_volume = sum((bar.volume for bar in prior[-5:]), Decimal("0")) / min(5, len(prior))
    volume_ok = avg_volume > 0 and latest.volume / avg_volume >= config.minimum_breakout_volume_ratio
    trigger = resistance * (Decimal("1") + config.breakout_buffer_percent / HUNDRED)
    stop = min(bar.low for bar in prior[-config.recent_swing_lookback:])
    if latest.close >= trigger and near and consolidation and volume_ok:
        return SetupDetection(kind, SetupState.TRIGGERED, Decimal("90"), trigger, stop, StopModel.RECENT_SWING_LOW, resistance)
    if near and consolidation:
        return SetupDetection(kind, SetupState.FORMING, Decimal("65"), trigger, stop, StopModel.RECENT_SWING_LOW, resistance,
                              (() if volume_ok else (ReasonCode.BREAKOUT_NOT_CONFIRMED,)))
    return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), resistance=resistance, reason_codes=(ReasonCode.NO_SETUP,))


def detect_micro_pullback(bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig()) -> SetupDetection:
    ordered = contiguous_tail(bars)
    kind = SetupType.MICRO_PULLBACK
    impulse_bars = 3
    required_bars = impulse_bars + config.minimum_pullback_bars + 1
    if len(ordered) < required_bars:
        return _unknown(kind)

    latest = ordered[-1]
    pullback_start = -(config.minimum_pullback_bars + 1)
    impulse_start = pullback_start - impulse_bars

    impulse = ordered[impulse_start:pullback_start]
    pullback = ordered[pullback_start:-1]
    impulse_change = (impulse[-1].high - impulse[0].open) / impulse[0].open * HUNDRED
    peak = impulse[-1].high
    depth = (peak - min(bar.low for bar in pullback)) / peak * HUNDRED
    controlled = all(bar.low >= impulse[0].open for bar in pullback) and pullback[-1].low >= pullback[0].low
    reduced_selling = pullback[-1].volume <= pullback[0].volume
    resistance = max(bar.high for bar in pullback)
    stop = min(bar.low for bar in pullback)
    trigger = resistance * (Decimal("1") + config.breakout_buffer_percent / HUNDRED)
    base_ok = impulse_change >= config.minimum_impulse_percent and depth <= config.maximum_micro_pullback_percent and controlled and reduced_selling
    if base_ok and latest.close >= trigger:
        return SetupDetection(kind, SetupState.TRIGGERED, Decimal("88"), trigger, stop, StopModel.MICRO_PULLBACK_LOW, resistance)
    if base_ok:
        return SetupDetection(kind, SetupState.FORMING, Decimal("70"), trigger, stop, StopModel.MICRO_PULLBACK_LOW, resistance,
                              (ReasonCode.BREAKOUT_NOT_CONFIRMED,))
    return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), reason_codes=(ReasonCode.NO_SETUP,))


def detect_bull_flag(bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig()) -> SetupDetection:
    ordered = contiguous_tail(bars)
    kind = SetupType.BULL_FLAG
    pole_bars = 4
    required_bars = pole_bars + config.minimum_consolidation_bars + 1
    if len(ordered) < required_bars:
        return _unknown(kind)

    latest = ordered[-1]
    flag_start = -(config.minimum_consolidation_bars + 1)
    pole_start = flag_start - pole_bars

    pole = ordered[pole_start:flag_start]
    flag = ordered[flag_start:-1]
    pole_low, pole_high = min(bar.low for bar in pole), max(bar.high for bar in pole)
    pole_range = pole_high - pole_low
    if pole_range <= 0:
        return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("0"), reason_codes=(ReasonCode.NO_SETUP,))
    impulse = pole_range / pole_low * HUNDRED
    flag_low = min(bar.low for bar in flag)
    retracement = (pole_high - flag_low) / pole_range
    controlled = flag[-1].low >= flag[0].low and max(bar.high for bar in flag) <= pole_high * Decimal("1.01")
    resistance = max(bar.high for bar in flag)
    trigger = resistance * (Decimal("1") + config.breakout_buffer_percent / HUNDRED)
    valid = (impulse >= config.minimum_impulse_percent and
             config.bull_flag_minimum_retracement <= retracement <= config.bull_flag_maximum_retracement and controlled)
    if valid and latest.close >= trigger:
        return SetupDetection(kind, SetupState.TRIGGERED, Decimal("92"), trigger, flag_low, StopModel.FLAG_LOW, resistance)
    if valid:
        return SetupDetection(kind, SetupState.FORMING, Decimal("72"), trigger, flag_low, StopModel.FLAG_LOW, resistance,
                              (ReasonCode.BREAKOUT_NOT_CONFIRMED,))
    return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), reason_codes=(ReasonCode.NO_SETUP,))


def detect_flat_top(bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig()) -> SetupDetection:
    ordered = contiguous_tail(bars)
    kind = SetupType.FLAT_TOP_BREAKOUT
    if len(ordered) < config.flat_top_tests + 3:
        return _unknown(kind)
    latest = ordered[-1]
    prior = ordered[-(config.flat_top_tests + 2):-1]
    resistance = max(bar.high for bar in prior)
    tolerance = resistance * config.flat_top_tolerance_percent / HUNDRED
    tests = tuple(bar for bar in prior if resistance - bar.high <= tolerance)
    lows = tuple(bar.low for bar in prior)
    higher_lows = lows[-1] >= lows[0]
    stop = min(lows)
    trigger = resistance * (Decimal("1") + config.breakout_buffer_percent / HUNDRED)
    valid = len(tests) >= config.flat_top_tests and higher_lows
    if valid and latest.close >= trigger:
        return SetupDetection(kind, SetupState.TRIGGERED, Decimal("86"), trigger, stop, StopModel.BREAKOUT_LEVEL, resistance)
    if valid:
        return SetupDetection(kind, SetupState.FORMING, Decimal("68"), trigger, stop, StopModel.BREAKOUT_LEVEL, resistance,
                              (ReasonCode.BREAKOUT_NOT_CONFIRMED,))
    return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), resistance=resistance, reason_codes=(ReasonCode.NO_SETUP,))


def detect_best_setup(bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig()) -> SetupDetection | None:
    detections = (
        detect_hod_breakout(bars, config),
        detect_micro_pullback(bars, config),
        detect_bull_flag(bars, config),
        detect_flat_top(bars, config),
    )

    actionable = tuple(
        item
        for item in detections
        if item.state in {SetupState.TRIGGERED, SetupState.FORMING}
    )

    if not actionable:
        return None

    priority = {
        SetupState.TRIGGERED: 2,
        SetupState.FORMING: 1,
    }

    return max(
        actionable,
        key=lambda item: (
            priority[item.state],
            item.score,
            item.setup_type.value,
        ),
    )


def hod_proximity(bars: tuple[MinuteBar, ...]) -> Decimal | None:
    features = build_features(bars)
    return None if features is None else features.distance_from_hod_percent


__all__ = ["detect_hod_breakout", "detect_micro_pullback", "detect_bull_flag", "detect_flat_top", "detect_best_setup", "hod_proximity"]
