from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.market_data.models import MarketEvent, MarketEventLog


class ReplayTiming(StrEnum): ORIGINAL = "ORIGINAL"; ACCELERATED = "ACCELERATED"; FIXED_STEP = "FIXED_STEP"


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    timing: ReplayTiming
    acceleration: Decimal = Decimal(1)
    fixed_step_microseconds: int = 0
    symbols: tuple[str, ...] = ()
    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReplayEmission:
    event: MarketEvent
    delay_microseconds: int


@dataclass(frozen=True, slots=True)
class ReplayState:
    events: tuple[MarketEvent, ...]
    config: ReplayConfig
    cursor: int = 0
    paused: bool = False
    previous_timestamp: datetime | None = None


def create_replay(log: MarketEventLog, config: ReplayConfig) -> ReplayState:
    _validate_config(config)
    events = tuple(item for item in log.events if (not config.symbols or item.symbol in config.symbols)
                   and (config.start_timestamp is None or item.timestamp >= config.start_timestamp)
                   and (config.end_timestamp is None or item.timestamp <= config.end_timestamp))
    return ReplayState(events, config)


def next_event(state: ReplayState) -> tuple[ReplayState, ReplayEmission | None]:
    if state.paused or state.cursor >= len(state.events): return state, None
    event = state.events[state.cursor]
    delay = _delay(state, event)
    return replace(state, cursor=state.cursor + 1, previous_timestamp=event.timestamp), ReplayEmission(event, delay)


def pause(state: ReplayState) -> ReplayState: return replace(state, paused=True)
def resume(state: ReplayState) -> ReplayState: return replace(state, paused=False)


def seek(state: ReplayState, *, sequence: int | None = None, timestamp: datetime | None = None) -> ReplayState:
    if (sequence is None) == (timestamp is None): raise ValueError("supply exactly one seek target")
    if timestamp is not None and timestamp.tzinfo is None: raise ValueError("seek timestamp must be timezone-aware")
    if sequence is not None:
        index = next((index for index, event in enumerate(state.events) if event.sequence >= sequence), len(state.events))
    else:
        index = next((index for index, event in enumerate(state.events) if event.timestamp >= timestamp), len(state.events))
    return replace(state, cursor=index, previous_timestamp=None)


def replay_all(state: ReplayState) -> tuple[ReplayState, tuple[ReplayEmission, ...]]:
    emissions = []
    while True:
        state, emission = next_event(state)
        if emission is None: return state, tuple(emissions)
        emissions.append(emission)


def _delay(state, event):
    if state.previous_timestamp is None: return 0
    elapsed = _micros(event.timestamp - state.previous_timestamp)
    if state.config.timing is ReplayTiming.ORIGINAL: return elapsed
    if state.config.timing is ReplayTiming.FIXED_STEP: return state.config.fixed_step_microseconds
    return int(Decimal(elapsed) / state.config.acceleration)


def _validate_config(config):
    if not isinstance(config, ReplayConfig) or not isinstance(config.timing, ReplayTiming): raise ValueError("ReplayConfig is invalid")
    if not isinstance(config.acceleration, Decimal) or not config.acceleration.is_finite() or config.acceleration <= 0: raise ValueError("acceleration must be positive")
    if not isinstance(config.fixed_step_microseconds, int) or isinstance(config.fixed_step_microseconds, bool) or config.fixed_step_microseconds < 0: raise ValueError("fixed step is invalid")
    if config.timing is ReplayTiming.FIXED_STEP and config.fixed_step_microseconds <= 0: raise ValueError("fixed-step replay requires a positive step")
    for value in (config.start_timestamp, config.end_timestamp):
        if value is not None and value.tzinfo is None: raise ValueError("filter timestamps must be timezone-aware")
    if config.start_timestamp and config.end_timestamp and config.start_timestamp > config.end_timestamp: raise ValueError("replay date range is invalid")
def _micros(value): return (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
