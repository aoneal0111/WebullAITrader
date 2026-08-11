"""Paper-only deterministic target and structural trailing calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR

from .configuration import TradeManagementConfig
from .models import MomentumEntrySignal, PaperExit


def planned_exits(signal: MomentumEntrySignal, shares: int, config: TradeManagementConfig = TradeManagementConfig()) -> tuple[PaperExit, ...]:
    if shares <= 0:
        raise ValueError("shares must be positive")
    first = int((Decimal(shares) * config.first_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
    second = int((Decimal(shares) * config.second_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
    runner = shares - first - second
    return (
        PaperExit("FIRST_TARGET", signal.entry_trigger + signal.risk_per_share * config.first_target_r, first),
        PaperExit("SECOND_TARGET", signal.entry_trigger + signal.risk_per_share * config.second_target_r, second),
        PaperExit("RUNNER", signal.target_levels[-1], runner),
    )


def managed_stop(signal: MomentumEntrySignal, *, highest_r: Decimal, structural_level: Decimal | None,
                 config: TradeManagementConfig = TradeManagementConfig()) -> Decimal:
    stop = signal.stop_price
    if highest_r >= config.move_stop_to_breakeven_after_r:
        stop = max(stop, signal.entry_trigger)
    if structural_level is not None and structural_level < signal.reference_price:
        stop = max(stop, structural_level)
    return stop


__all__ = ["planned_exits", "managed_stop"]
