"""Mechanical one-minute setup detectors; no discretionary chart inference."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256

from .configuration import SetupConfig
from .features import build_features, contiguous_tail
from .models import MinuteBar, ReasonCode, SetupDetection, SetupState, SetupType, StopModel

HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class AccelerationPoint:
    timestamp: object
    price: Decimal
    volume: Decimal
    dollar_volume: Decimal
    spread_percent: Decimal | None
    tradable: bool
    halted: bool
    fresh: bool = True


def _structural_episode_id(kind: SetupType, bars: tuple[MinuteBar, ...], anchor_count: int) -> str:
    """Identify the detector's current completed-bar structure.

    The bar timestamps are sequence identity/provenance, not a cooldown or a
    wall-clock reset.  Prices and quotes are deliberately excluded so a
    repeated evaluation of one episode remains the same opportunity.
    """
    # Locate the latest completed breakout transition.  Its origin is the
    # bar that established the prior structural high, so later continuation
    # bars do not slide the identity.  A later breakout transition creates a
    # new same-family episode.  Prices select provenance only; timestamps own
    # the identity.
    evidence = bars
    pivot = None
    for index in range(1, len(evidence)):
        prior = evidence[:index]
        resistance = max(item.high for item in prior)
        if evidence[index].close > resistance and evidence[index].high >= resistance:
            pivot = max(prior, key=lambda item: item.high)
    if pivot is None:
        pivot = max(evidence, key=lambda item: item.high)
    material = "|".join((kind.value, pivot.timestamp.isoformat()))
    return "LEGACY_EPISODE|" + sha256(material.encode()).hexdigest()


def _with_episode(detection: SetupDetection, bars: tuple[MinuteBar, ...], anchor_count: int) -> SetupDetection:
    return replace(
        detection,
        structural_episode_id=_structural_episode_id(detection.setup_type, bars, anchor_count),
        structural_anchor=_structural_anchor(detection.setup_type, bars, anchor_count),
    )


def _structural_anchor(kind: SetupType, bars: tuple[MinuteBar, ...], anchor_count: int) -> str:
    """Return detector provenance for the current setup origin."""
    evidence = bars
    origin = None
    for index in range(1, len(evidence)):
        prior = evidence[:index]
        resistance = max(item.high for item in prior)
        if evidence[index].close > resistance and evidence[index].high >= resistance:
            origin = max(prior, key=lambda item: item.high)
    if origin is None:
        origin = max(evidence[:-1] or evidence, key=lambda item: item.high)
    return "|".join((kind.value, origin.timestamp.isoformat()))


class LegacySetupEpisodeTracker:
    """Bounded detector-owned current episode state for legacy Warrior."""

    def __init__(self, *, maximum_symbols: int = 500) -> None:
        if maximum_symbols <= 0:
            raise ValueError("maximum legacy episode capacity must be positive")
        self.maximum_symbols = maximum_symbols
        self._current: OrderedDict[str, tuple[SetupType, str, str, str]] = OrderedDict()

    def observe(self, symbol: str, setup: SetupDetection, *, session: str) -> SetupDetection:
        normalized = symbol.strip().upper()
        normalized_session = session.strip().upper()
        anchor = setup.structural_anchor or setup.structural_episode_id or setup.setup_type.value
        prior = self._current.get(normalized)
        if (
            prior is not None
            and prior[0] is setup.setup_type
            and prior[1] == anchor
            and prior[3] == normalized_session
        ):
            episode_id = prior[2]
        else:
            episode_id = _episode_token(normalized, normalized_session, setup.setup_type, anchor)
        self._current[normalized] = (setup.setup_type, anchor, episode_id, normalized_session)
        self._current.move_to_end(normalized)
        while len(self._current) > self.maximum_symbols:
            self._current.popitem(last=False)
        return replace(setup, structural_episode_id=episode_id, structural_anchor=anchor)

    def invalidate(self, symbol: str) -> None:
        self._current.pop(symbol.strip().upper(), None)


def _episode_token(symbol: str, session: str, kind: SetupType, anchor: str) -> str:
    material = "|".join(("LEGACY_EPISODE_V2", symbol, session.strip().upper(), kind.value, anchor))
    return "LEGACY_EPISODE|" + sha256(material.encode()).hexdigest()


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
        return _with_episode(SetupDetection(kind, SetupState.TRIGGERED, Decimal("90"), trigger, stop, StopModel.RECENT_SWING_LOW, resistance), ordered, config.minimum_consolidation_bars + 1)
    if near and consolidation:
        return _with_episode(SetupDetection(kind, SetupState.FORMING, Decimal("65"), trigger, stop, StopModel.RECENT_SWING_LOW, resistance,
                              (() if volume_ok else (ReasonCode.BREAKOUT_NOT_CONFIRMED,))), ordered, config.minimum_consolidation_bars + 1)
    return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), resistance=resistance, reason_codes=(ReasonCode.NO_SETUP,))


def detect_micro_pullback(bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig()) -> SetupDetection:
    ordered = contiguous_tail(bars)
    kind = SetupType.MICRO_PULLBACK
    impulse_bars = 3
    required_bars = impulse_bars + config.minimum_pullback_bars + 1
    if len(ordered) < required_bars:
        return _unknown(kind)

    latest = ordered[-1]
    maximum_pullback_bars = min(
        config.maximum_pullback_bars,
        len(ordered) - impulse_bars - 1,
    )
    for pullback_bars in range(
        config.minimum_pullback_bars,
        maximum_pullback_bars + 1,
    ):
        pullback_start = -(pullback_bars + 1)
        impulse_start = pullback_start - impulse_bars
        impulse = ordered[impulse_start:pullback_start]
        pullback = ordered[pullback_start:-1]
        impulse_change = (
            (impulse[-1].high - impulse[0].open)
            / impulse[0].open
            * HUNDRED
        )
        peak = impulse[-1].high
        depth = (peak - min(bar.low for bar in pullback)) / peak * HUNDRED
        controlled = (
            all(bar.low >= impulse[0].open for bar in pullback)
            and pullback[-1].low >= pullback[0].low
        )
        reduced_selling = pullback[-1].volume <= pullback[0].volume
        resistance = max(bar.high for bar in pullback)
        stop = min(bar.low for bar in pullback)
        trigger = resistance * (
            Decimal("1") + config.breakout_buffer_percent / HUNDRED
        )
        base_ok = (
            impulse_change >= config.minimum_impulse_percent
            and depth <= config.maximum_micro_pullback_percent
            and controlled
            and reduced_selling
        )
        if not base_ok:
            continue
        if latest.close >= trigger:
            return _with_episode(
                SetupDetection(
                    kind, SetupState.TRIGGERED, Decimal("88"), trigger, stop,
                    StopModel.MICRO_PULLBACK_LOW, resistance,
                ),
                ordered,
                impulse_bars + pullback_bars + 1,
            )
        return _with_episode(
            SetupDetection(
                kind, SetupState.FORMING, Decimal("70"), trigger, stop,
                StopModel.MICRO_PULLBACK_LOW, resistance,
                (ReasonCode.BREAKOUT_NOT_CONFIRMED,),
            ),
            ordered,
            impulse_bars + pullback_bars + 1,
        )
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
        return _with_episode(SetupDetection(kind, SetupState.TRIGGERED, Decimal("92"), trigger, flag_low, StopModel.FLAG_LOW, resistance), ordered, required_bars)
    if valid:
        return _with_episode(SetupDetection(kind, SetupState.FORMING, Decimal("72"), trigger, flag_low, StopModel.FLAG_LOW, resistance,
                              (ReasonCode.BREAKOUT_NOT_CONFIRMED,)), ordered, required_bars)
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
        return _with_episode(SetupDetection(kind, SetupState.TRIGGERED, Decimal("86"), trigger, stop, StopModel.BREAKOUT_LEVEL, resistance), ordered, config.flat_top_tests + 2)
    if valid:
        return _with_episode(SetupDetection(kind, SetupState.FORMING, Decimal("68"), trigger, stop, StopModel.BREAKOUT_LEVEL, resistance,
                              (ReasonCode.BREAKOUT_NOT_CONFIRMED,)), ordered, config.flat_top_tests + 2)
    return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), resistance=resistance, reason_codes=(ReasonCode.NO_SETUP,))


def detect_momentum_acceleration(
    points: tuple[AccelerationPoint, ...],
    config: SetupConfig = SetupConfig(),
) -> SetupDetection:
    """Detect a bounded live acceleration episode without requiring a minute bar."""
    kind = SetupType.MOMENTUM_ACCELERATION
    if len(points) < 3:
        return _unknown(kind)
    ordered = points[-8:]
    latest = ordered[-1]
    if (latest.timestamp - ordered[0].timestamp).total_seconds() > 30:
        return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("0"), reason_codes=(ReasonCode.NO_SETUP,))
    if any(not item.fresh or not item.tradable or item.halted for item in ordered):
        return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("0"), reason_codes=(ReasonCode.NO_SETUP,))
    elapsed = max(Decimal("0.1"), Decimal(str((latest.timestamp - ordered[0].timestamp).total_seconds())) / Decimal("60"))
    half = max(1, len(ordered) // 2)
    first, prior = ordered[0], ordered[:-1]
    first_elapsed = max(Decimal("0.1"), Decimal(str((ordered[half - 1].timestamp - first.timestamp).total_seconds())) / Decimal("60"))
    first_velocity = (ordered[half - 1].price - first.price) / first.price * HUNDRED / first_elapsed
    velocity = (latest.price - first.price) / first.price * HUNDRED / elapsed
    acceleration = velocity - first_velocity
    prior_high = max(item.price for item in prior)
    base = min(item.price for item in ordered[:-1])
    trigger = prior_high * (Decimal("1") + config.breakout_buffer_percent / HUNDRED)
    prior_volume = sum(item.volume for item in ordered[:-1]) / Decimal(len(ordered) - 1)
    prior_dollar = sum(item.dollar_volume for item in ordered[:-1]) / Decimal(len(ordered) - 1)
    participation_velocity = (latest.volume - first.volume) / elapsed
    dollar_volume_velocity = (latest.dollar_volume - first.dollar_volume) / elapsed
    volume_progress = latest.volume >= max(first.volume, prior_volume * Decimal("1.05"))
    dollar_progress = latest.dollar_volume >= max(first.dollar_volume, prior_dollar * Decimal("1.05"))
    positive_steps = sum(1 for left, right in zip(ordered, ordered[1:]) if right.price > left.price)
    supportive = (
        positive_steps >= 2 and velocity >= Decimal("0.5") and acceleration >= Decimal("0.05")
        and volume_progress and dollar_progress
        and participation_velocity > 0 and dollar_volume_velocity > 0
        and latest.price > first.price
    )
    if not supportive:
        return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), resistance=prior_high, reason_codes=(ReasonCode.NO_SETUP,))
    episode = "MOMENTUM_ACCELERATION|" + sha256(f"{ordered[0].timestamp.isoformat()}|{base}".encode()).hexdigest()
    anchor = f"MOMENTUM_ACCELERATION|{ordered[0].timestamp.isoformat()}"
    state = SetupState.TRIGGERED if latest.price >= trigger and len(ordered) >= 4 else SetupState.FORMING
    score = Decimal("84") if state is SetupState.TRIGGERED else Decimal("68")
    reasons = () if state is SetupState.TRIGGERED else (ReasonCode.BREAKOUT_NOT_CONFIRMED,)
    return SetupDetection(
        kind, state, score, trigger, base, StopModel.RECENT_SWING_LOW, prior_high,
        reasons, structural_episode_id=episode, structural_anchor=anchor,
    )


def _live_points_valid(points: tuple[AccelerationPoint, ...], *, lifetime_seconds: int) -> bool:
    if len(points) < 5:
        return False
    ordered = points[-8:]
    latest = ordered[-1]
    if (latest.timestamp - ordered[0].timestamp).total_seconds() > lifetime_seconds:
        return False
    if any(not item.fresh or not item.tradable or item.halted for item in ordered):
        return False
    # Spread is execution quality, not technical structure. Quote validity
    # and freshness are enforced before execution.
    return True


def _continuation_detection(
    points: tuple[AccelerationPoint, ...], config: SetupConfig, *, reclaim: bool,
) -> SetupDetection:
    """Detect a bounded pause-then-expansion continuation episode."""
    kind = SetupType.RECLAIM_CONTINUATION if reclaim else SetupType.MOMENTUM_REACCELERATION
    if not _live_points_valid(points, lifetime_seconds=45):
        return _unknown(kind)
    ordered = points[-8:]
    latest = ordered[-1]
    prior = ordered[:-1]
    level = max(item.price for item in prior[:-2])
    pause = ordered[-3]
    base = min(item.price for item in ordered[-4:])
    # A continuation needs a real pause/absorption phase, not merely a second
    # rising tick. Reclaim additionally requires price to lose the short level.
    pause_seen = pause.price <= level * Decimal("0.998")
    lost_level = any(item.price < level * Decimal("0.998") for item in ordered[1:-1])
    rising_tail = ordered[-2].price > pause.price and latest.price > ordered[-2].price
    trigger = level * (Decimal("1") + config.breakout_buffer_percent / HUNDRED)
    velocity = (latest.price - pause.price) / pause.price * HUNDRED
    elapsed = max(Decimal("0.1"), Decimal(str((latest.timestamp - pause.timestamp).total_seconds())) / Decimal("60"))
    volume_velocity = (latest.volume - pause.volume) / elapsed
    dollar_velocity = (latest.dollar_volume - pause.dollar_volume) / elapsed
    participation = latest.volume >= max(pause.volume, sum(item.volume for item in prior[-3:]) / Decimal(min(3, len(prior))))
    dollar_progress = latest.dollar_volume >= max(pause.dollar_volume, sum(item.dollar_volume for item in prior[-3:]) / Decimal(min(3, len(prior))))
    structure_ok = pause_seen and rising_tail and (lost_level if reclaim else True)
    supportive = structure_ok and velocity >= Decimal("0.25") and participation and dollar_progress and volume_velocity > 0 and dollar_velocity > 0
    if not supportive:
        return SetupDetection(kind, SetupState.NOT_FORMED, Decimal("10"), resistance=level, reason_codes=(ReasonCode.NO_SETUP,))
    episode_anchor = ordered[-4].timestamp.isoformat()
    episode = f"{kind.value}|" + sha256(f"{episode_anchor}|{base}".encode()).hexdigest()
    anchor = f"{kind.value}|{episode_anchor}"
    state = SetupState.TRIGGERED if latest.price >= trigger else SetupState.FORMING
    score = Decimal("83") if state is SetupState.TRIGGERED else Decimal("67")
    reasons = () if state is SetupState.TRIGGERED else (ReasonCode.BREAKOUT_NOT_CONFIRMED,)
    return SetupDetection(
        kind, state, score, trigger, base, StopModel.RECENT_SWING_LOW, level,
        reasons, structural_episode_id=episode, structural_anchor=anchor,
    )


def detect_momentum_reacceleration(points: tuple[AccelerationPoint, ...], config: SetupConfig = SetupConfig()) -> SetupDetection:
    return _continuation_detection(points, config, reclaim=False)


def detect_reclaim_continuation(points: tuple[AccelerationPoint, ...], config: SetupConfig = SetupConfig()) -> SetupDetection:
    return _continuation_detection(points, config, reclaim=True)


def detect_best_setup(
    bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig(),
    acceleration_points: tuple[AccelerationPoint, ...] = (),
) -> SetupDetection | None:
    detections = (
        detect_hod_breakout(bars, config),
        detect_micro_pullback(bars, config),
        detect_bull_flag(bars, config),
        detect_flat_top(bars, config),
        detect_momentum_acceleration(acceleration_points, config),
        detect_momentum_reacceleration(acceleration_points, config),
        detect_reclaim_continuation(acceleration_points, config),
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
            item.setup_type is not SetupType.MOMENTUM_ACCELERATION,
            item.score,
            item.setup_type.value,
        ),
    )


def hod_proximity(bars: tuple[MinuteBar, ...]) -> Decimal | None:
    features = build_features(bars)
    return None if features is None else features.distance_from_hod_percent


__all__ = ["AccelerationPoint", "LegacySetupEpisodeTracker", "detect_hod_breakout", "detect_micro_pullback", "detect_bull_flag", "detect_flat_top", "detect_momentum_acceleration", "detect_momentum_reacceleration", "detect_reclaim_continuation", "detect_best_setup", "hod_proximity"]
