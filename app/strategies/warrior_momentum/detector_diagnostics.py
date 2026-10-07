"""Bounded, failure-contained per-symbol Warrior detector diagnostics.

This module is deliberately observational.  It never changes detector output or
candidate selection and keeps only the latest state plus a short transition tail.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .configuration import SetupConfig
from .models import SetupDetection, SetupState, SetupType
from .setups import (
    AccelerationPoint, detect_bull_flag, detect_flat_top, detect_hod_breakout,
    detect_micro_pullback, detect_momentum_acceleration,
    detect_momentum_reacceleration, detect_reclaim_continuation,
)

MAX_SYMBOLS = 512
MAX_TRANSITIONS = 8
MAX_EPISODES = 4096


@dataclass(frozen=True, slots=True)
class DetectorDiagnostic:
    symbol: str
    detector: str
    state: str
    first_failed_predicate: str | None
    evaluation_timestamp: datetime
    structural_episode_id: str | None = None
    trigger: str | None = None
    stop: str | None = None
    score: str | None = None
    summary: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SetupTransition:
    timestamp: datetime
    setup_type: str
    state: str
    reason: str | None
    structural_episode_id: str | None


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _summary(points: tuple[AccelerationPoint, ...], *, reacceleration: bool = False,
             reclaim: bool = False) -> dict[str, Any]:
    if not points:
        return {"observation_count": 0}
    ordered = points[-8:]
    first, latest = ordered[0], ordered[-1]
    age = (latest.timestamp - first.timestamp).total_seconds()
    out: dict[str, Any] = {
        "observation_count": len(points), "window_age_seconds": age,
        "first_price": _decimal(first.price), "latest_price": _decimal(latest.price),
        "positive_price_steps": sum(1 for a, b in zip(ordered, ordered[1:]) if b.price > a.price),
        "spread": _decimal(latest.spread_percent), "freshness": all(p.fresh for p in ordered),
        "lifetime_status": "WITHIN_WINDOW",
    }
    if reacceleration:
        pause = ordered[-3] if len(ordered) >= 3 else latest
        elapsed = max(Decimal("0.1"), Decimal(str((latest.timestamp - pause.timestamp).total_seconds())) / Decimal("60"))
        level = max((p.price for p in ordered[:-2]), default=pause.price)
        out.update({
            "pause_detected": pause.price <= level * Decimal("0.998"),
            "rising_tail": len(ordered) >= 2 and latest.price > ordered[-2].price,
            "velocity": _decimal((latest.price - pause.price) / pause.price * Decimal("100") / elapsed),
            "participation_progression": latest.volume >= pause.volume,
            "dollar_volume_progression": latest.dollar_volume >= pause.dollar_volume,
            "volume_velocity": _decimal((latest.volume - pause.volume) / elapsed),
            "dollar_volume_velocity": _decimal((latest.dollar_volume - pause.dollar_volume) / elapsed),
        })
    else:
        elapsed = max(Decimal("0.1"), Decimal(str((latest.timestamp - first.timestamp).total_seconds())) / Decimal("60"))
        half = max(1, len(ordered) // 2)
        first_elapsed = max(Decimal("0.1"), Decimal(str((ordered[half - 1].timestamp - first.timestamp).total_seconds())) / Decimal("60"))
        velocity = (latest.price - first.price) / first.price * Decimal("100") / elapsed
        first_velocity = (ordered[half - 1].price - first.price) / first.price * Decimal("100") / first_elapsed
        prior_volume = sum(p.volume for p in ordered[:-1]) / Decimal(max(1, len(ordered) - 1))
        prior_dollar = sum(p.dollar_volume for p in ordered[:-1]) / Decimal(max(1, len(ordered) - 1))
        out.update({
            "velocity": _decimal(velocity), "required_velocity": "0.5",
            "acceleration": _decimal(velocity - first_velocity), "required_acceleration": "0.05",
            "volume_progression": latest.volume >= max(first.volume, prior_volume * Decimal("1.05")),
            "required_volume_progression": "1.05",
            "dollar_volume_progression": latest.dollar_volume >= max(first.dollar_volume, prior_dollar * Decimal("1.05")),
            "required_dollar_progression": "1.05",
            "participation_velocity": _decimal((latest.volume - first.volume) / elapsed),
            "dollar_volume_velocity": _decimal((latest.dollar_volume - first.dollar_volume) / elapsed),
        })
    if reclaim:
        out["level_lost"] = any(p.price < max((q.price for q in ordered[:-2]), default=latest.price) * Decimal("0.998") for p in ordered[1:-1])
        out["reclaim_observed"] = latest.price >= max((q.price for q in ordered[:-2]), default=latest.price)
    return out


def _first_live_failure(points: tuple[AccelerationPoint, ...], config: SetupConfig,
                        *, reacceleration: bool = False, reclaim: bool = False) -> tuple[str | None, dict[str, Any]]:
    summary = _summary(points, reacceleration=reacceleration, reclaim=reclaim)
    minimum = 5 if reacceleration else 3
    lifetime = 45 if reacceleration else 30
    if len(points) < minimum:
        return "INSUFFICIENT_POINTS", summary
    ordered = points[-8:]
    if (ordered[-1].timestamp - ordered[0].timestamp).total_seconds() > lifetime:
        summary["lifetime_status"] = "EXCEEDED"
        return "LIFETIME_EXCEEDED", summary
    if any(not p.fresh for p in ordered): return "FRESHNESS_FAILED", summary
    if any(not p.tradable for p in ordered): return "TRADABILITY_FAILED", summary
    if any(p.halted for p in ordered): return "HALT_FAILED", summary
    if reacceleration:
        if not summary.get("pause_detected"): return "PAUSE_NOT_DETECTED", summary
        if not summary.get("rising_tail"): return "RISING_TAIL_FAILED", summary
        if reclaim and not summary.get("level_lost"): return "LEVEL_NOT_LOST", summary
        if Decimal(summary["velocity"]) < Decimal("0.25"): return "VELOCITY_FAILED", summary
        if not summary.get("participation_progression"): return "PARTICIPATION_FAILED", summary
        if not summary.get("dollar_volume_progression"): return "DOLLAR_VOLUME_PROGRESSION_FAILED", summary
        if Decimal(summary["volume_velocity"]) <= 0: return "PARTICIPATION_VELOCITY_FAILED", summary
        if Decimal(summary["dollar_volume_velocity"]) <= 0: return "DOLLAR_VOLUME_VELOCITY_FAILED", summary
    else:
        if summary["positive_price_steps"] < 2: return "POSITIVE_STEPS_FAILED", summary
        if Decimal(summary["velocity"]) < Decimal("0.5"): return "VELOCITY_FAILED", summary
        if Decimal(summary["acceleration"]) < Decimal("0.05"): return "ACCELERATION_FAILED", summary
        if not summary["volume_progression"]: return "VOLUME_PROGRESSION_FAILED", summary
        if not summary["dollar_volume_progression"]: return "DOLLAR_VOLUME_PROGRESSION_FAILED", summary
        if Decimal(summary["participation_velocity"]) <= 0: return "PARTICIPATION_VELOCITY_FAILED", summary
        if Decimal(summary["dollar_volume_velocity"]) <= 0: return "DOLLAR_VOLUME_VELOCITY_FAILED", summary
    return None, summary


class BoundedSetupDiagnostics:
    """Latest detector state and a bounded transition tail per symbol."""

    def __init__(self, *, maximum_symbols: int = MAX_SYMBOLS, maximum_transitions: int = MAX_TRANSITIONS) -> None:
        self.maximum_symbols = maximum_symbols
        self.maximum_transitions = maximum_transitions
        self._day: dict[str, tuple[date, str]] = {}
        self._latest: OrderedDict[str, dict[str, DetectorDiagnostic]] = OrderedDict()
        self._transitions: OrderedDict[str, deque[SetupTransition]] = OrderedDict()
        self._episodes: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self._triggered_episodes: OrderedDict[tuple[str, str, str], None] = OrderedDict()
        self.unique_setup_episodes: dict[str, int] = {}
        self.unique_triggered_episodes: dict[str, int] = {}

    def observe(self, symbol: str, bars: tuple, points: tuple[AccelerationPoint, ...], *, session: str,
                timestamp: datetime, config: SetupConfig, selected_setup: SetupDetection | None) -> None:
        try:
            symbol = symbol.strip().upper()
            key = (timestamp.date(), session)
            if self._day.get(symbol) != key:
                self._day[symbol] = key
                self._latest.pop(symbol, None); self._transitions.pop(symbol, None)
            detections = (
                detect_hod_breakout(bars, config), detect_micro_pullback(bars, config),
                detect_bull_flag(bars, config), detect_flat_top(bars, config),
                detect_momentum_acceleration(points, config), detect_momentum_reacceleration(points, config),
                detect_reclaim_continuation(points, config),
            )
            latest = self._latest.setdefault(symbol, {})
            for detection in detections:
                name = detection.setup_type.value
                failure = None
                summary = None
                if detection.setup_type is SetupType.MOMENTUM_ACCELERATION:
                    failure, summary = _first_live_failure(points, config)
                elif detection.setup_type is SetupType.MOMENTUM_REACCELERATION:
                    failure, summary = _first_live_failure(points, config, reacceleration=True)
                elif detection.setup_type is SetupType.RECLAIM_CONTINUATION:
                    failure, summary = _first_live_failure(points, config, reacceleration=True, reclaim=True)
                elif detection.state in {SetupState.NOT_FORMED, SetupState.UNKNOWN}:
                    failure = "HISTORY_INSUFFICIENT" if detection.state is SetupState.UNKNOWN else "STRUCTURE_NOT_FORMED"
                prior_detail = latest.get(name)
                invalidated = (
                    prior_detail is not None
                    and prior_detail.state in {SetupState.FORMING.value, SetupState.TRIGGERED.value}
                    and detection.state is SetupState.NOT_FORMED
                )
                if selected_setup is not None and detection.state in {SetupState.FORMING, SetupState.TRIGGERED} and detection.setup_type is not selected_setup.setup_type:
                    state = "MASKED_BY_STRONGER_SETUP"; failure = "MASKED_BY_STRONGER_SETUP"
                elif invalidated:
                    state = "INVALIDATED"
                else:
                    state = detection.state.value
                latest[name] = DetectorDiagnostic(symbol, name, state, failure, timestamp,
                    detection.structural_episode_id, _decimal(detection.trigger), _decimal(detection.stop_price), _decimal(detection.score), summary)
                self._record_transition(symbol, detection, state, failure, timestamp)
            self._latest.move_to_end(symbol)
            while len(self._latest) > self.maximum_symbols:
                old, _ = self._latest.popitem(last=False); self._transitions.pop(old, None); self._day.pop(old, None)
        except Exception:
            return None

    def _record_transition(self, symbol: str, detection: SetupDetection, state: str, reason: str | None, timestamp: datetime) -> None:
        tail = self._transitions.setdefault(symbol, deque(maxlen=self.maximum_transitions))
        episode = detection.structural_episode_id
        transition = SetupTransition(timestamp, detection.setup_type.value, state, reason, episode)
        if not tail or tail[-1] != transition:
            tail.append(transition)
        if episode:
            key = (symbol, detection.setup_type.value, episode)
            if key not in self._episodes:
                self._episodes[key] = None
                self.unique_setup_episodes[detection.setup_type.value] = self.unique_setup_episodes.get(detection.setup_type.value, 0) + 1
            if detection.state is SetupState.TRIGGERED and key not in self._triggered_episodes:
                self._triggered_episodes[key] = None
                self.unique_triggered_episodes[detection.setup_type.value] = self.unique_triggered_episodes.get(detection.setup_type.value, 0) + 1
        while len(self._episodes) > MAX_EPISODES:
            self._episodes.popitem(last=False)
        while len(self._triggered_episodes) > MAX_EPISODES:
            self._triggered_episodes.popitem(last=False)

    def snapshot(self, symbol: str | None = None) -> dict[str, Any]:
        symbols = [symbol.strip().upper()] if symbol else list(self._latest)
        return {s: {k: asdict(v) for k, v in self._latest.get(s, {}).items()} for s in symbols if s in self._latest}

    def transitions(self, symbol: str) -> tuple[SetupTransition, ...]:
        return tuple(self._transitions.get(symbol.strip().upper(), ()))

    def all_transitions(self) -> dict[str, tuple[SetupTransition, ...]]:
        return {
            symbol: tuple(asdict(value) for value in values)
            for symbol, values in self._transitions.items()
        }

    def unique_episode_counts(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "unique_setup_episodes": self.unique_setup_episodes.get(name, 0),
                "unique_triggered_episodes": self.unique_triggered_episodes.get(name, 0),
            }
            for name in {key[1] for key in self._episodes}
        }


__all__ = ["BoundedSetupDiagnostics", "DetectorDiagnostic", "SetupTransition"]
