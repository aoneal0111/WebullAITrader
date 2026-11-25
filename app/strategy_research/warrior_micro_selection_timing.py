"""Offline, prefix-only comparison of Micro Pullback and selected Warrior setup.

Produces observed structural decisions, not quotes, fills, execution permissions
or recommendations. Does not access data providers, brokers, or PAPER ledgers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.strategies.warrior_momentum.models import MinuteBar, SetupState, SetupType
from app.strategies.warrior_momentum.setups import (
    detect_best_setup, detect_micro_pullback,
)
from app.strategies.warrior_momentum.configuration import SetupConfig


@dataclass(frozen=True, slots=True)
class MicroSelectionObservation:
    at: datetime
    micro_trigger: Decimal
    selected_trigger: Decimal | None
    selected_type: str | None
    micro_close: Decimal
    trigger_premium: Decimal | None


def review_micro_selection(
    bars: tuple[MinuteBar, ...], config: SetupConfig = SetupConfig(),
) -> tuple[MicroSelectionObservation, ...]:
    """Record each completed-bar micro trigger and the *same-prefix* selection.

    Rows only exist when Micro Pullback is TRIGGERED, not merely FORMING.
    Trigger premium measures the selected trigger minus micro trigger; it
    is not slippage or a traded price. No future bars enter any decision.
    """
    if len(bars) > 600:
        raise ValueError("BAR_LIMIT_EXCEEDED")
    if any(
        left.symbol != right.symbol or left.timestamp >= right.timestamp
        for left, right in zip(bars, bars[1:])
    ):
        raise ValueError("SINGLE_SYMBOL_STRICT_TIME_REQUIRED")
    results = []
    for end in range(1, len(bars) + 1):
        prefix = bars[:end]
        micro = detect_micro_pullback(prefix, config)
        if micro.state is not SetupState.TRIGGERED or micro.trigger is None:
            continue
        selected = detect_best_setup(prefix, config)
        trigger = None if selected is None else selected.trigger
        results.append(MicroSelectionObservation(
            at=prefix[-1].timestamp,
            micro_trigger=micro.trigger,
            selected_trigger=trigger,
            selected_type=None if selected is None else selected.setup_type.value,
            micro_close=prefix[-1].close,
            trigger_premium=None if trigger is None else trigger - micro.trigger,
        ))
    return tuple(results)
