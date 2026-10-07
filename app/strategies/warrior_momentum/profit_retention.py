"""Executable-BID profit-state policy shared by Warrior and Quick Scalper."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from .quick_scalper import StrategyOwner

ZERO = Decimal("0")


class ProfitState(StrEnum):
    NO_PROFIT = "NO_PROFIT"
    INITIAL_FAVORABLE = "INITIAL_FAVORABLE"
    PROFIT_ESTABLISHED = "PROFIT_ESTABLISHED"
    PROFIT_DEFENSE_ACTIVE = "PROFIT_DEFENSE_ACTIVE"
    TARGET_PARTIAL = "TARGET_PARTIAL"
    RUNNER = "RUNNER"
    EXITED = "EXITED"


class ProfitAction(StrEnum):
    NONE = "NONE"
    TIGHTEN_STOP = "TIGHTEN_STOP"
    FULL_EXIT = "FULL_EXIT"


@dataclass(frozen=True, slots=True)
class ProfitRetentionPosition:
    strategy: StrategyOwner
    symbol: str
    lifecycle_id: str
    entered_at: datetime
    entry_price: Decimal
    initial_stop: Decimal
    current_stop: Decimal
    peak_executable_bid: Decimal
    state: ProfitState = ProfitState.NO_PROFIT
    target_partial_taken: bool = False


@dataclass(frozen=True, slots=True)
class ProfitRetentionDecision:
    position: ProfitRetentionPosition
    action: ProfitAction
    desired_stop: Decimal | None
    reason: str
    current_r: Decimal
    peak_r: Decimal
    giveback_r: Decimal


@dataclass(frozen=True, slots=True)
class ProfitRetentionConfig:
    warrior_activation_r: Decimal = Decimal("1.50")
    warrior_giveback_r: Decimal = Decimal("0.45")
    scalp_activation_r: Decimal = Decimal("0.50")
    scalp_giveback_r: Decimal = Decimal("0.25")
    scalp_minimum_profit: Decimal = Decimal("0.05")
    scalp_maximum_holding_seconds: int = 300


class ProfitRetentionPolicy:
    """Produces intent only; canonical stop/exit services perform mutations."""

    def __init__(self, config: ProfitRetentionConfig = ProfitRetentionConfig()) -> None:
        self.config = config

    def evaluate(
        self, position: ProfitRetentionPosition, *, executable_bid: Decimal,
        observed_at: datetime, momentum_stalled: bool = False,
    ) -> ProfitRetentionDecision:
        if executable_bid <= ZERO or observed_at.tzinfo is None:
            raise ValueError("fresh executable BID and aware timestamp are required")
        risk = position.entry_price - position.initial_stop
        if risk <= ZERO:
            raise ValueError("profit retention requires positive initial risk")
        peak = max(position.peak_executable_bid, executable_bid)
        current_r = (executable_bid - position.entry_price) / risk
        peak_r = (peak - position.entry_price) / risk
        giveback_r = max(ZERO, peak_r - current_r)
        state = position.state
        if peak_r > ZERO:
            state = ProfitState.INITIAL_FAVORABLE
        activation = (
            self.config.scalp_activation_r
            if position.strategy is StrategyOwner.QUICK_SCALPER
            else self.config.warrior_activation_r
        )
        allowance = (
            self.config.scalp_giveback_r
            if position.strategy is StrategyOwner.QUICK_SCALPER
            else self.config.warrior_giveback_r
        )
        if peak_r >= activation:
            state = ProfitState.PROFIT_ESTABLISHED
        updated = replace(position, peak_executable_bid=peak, state=state)

        if position.strategy is StrategyOwner.QUICK_SCALPER:
            held = observed_at - position.entered_at
            profitable = executable_bid - position.entry_price >= self.config.scalp_minimum_profit
            if held >= timedelta(seconds=self.config.scalp_maximum_holding_seconds):
                return ProfitRetentionDecision(
                    replace(updated, state=ProfitState.PROFIT_DEFENSE_ACTIVE),
                    ProfitAction.FULL_EXIT, None, "SCALP_MAX_HOLD", current_r,
                    peak_r, giveback_r,
                )
            if profitable and momentum_stalled:
                return ProfitRetentionDecision(
                    replace(updated, state=ProfitState.PROFIT_DEFENSE_ACTIVE),
                    ProfitAction.FULL_EXIT, None, "SCALP_MOMENTUM_STALL",
                    current_r, peak_r, giveback_r,
                )

        if peak_r >= activation and giveback_r >= allowance and current_r > ZERO:
            desired = position.entry_price + max(ZERO, peak_r - allowance) * risk
            desired = min(desired, executable_bid)
            if desired > position.current_stop:
                return ProfitRetentionDecision(
                    replace(updated, state=ProfitState.PROFIT_DEFENSE_ACTIVE),
                    ProfitAction.TIGHTEN_STOP, desired, "MFE_GIVEBACK_DEFENSE",
                    current_r, peak_r, giveback_r,
                )
        return ProfitRetentionDecision(
            updated, ProfitAction.NONE, None, "NO_ACTION",
            current_r, peak_r, giveback_r,
        )


__all__ = [
    "ProfitAction", "ProfitRetentionConfig", "ProfitRetentionDecision",
    "ProfitRetentionPolicy", "ProfitRetentionPosition", "ProfitState",
]
