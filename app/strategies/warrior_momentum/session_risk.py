"""Session-boundary risk decisions for autonomous PAPER positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from app.market.calendar import EASTERN, is_trading_day

from .configuration import SessionManagementConfig


@dataclass(frozen=True, slots=True)
class OvernightCarryAssessment:
    carry: bool
    reasons: tuple[str, ...]


def minutes_until_overnight(value: datetime) -> Decimal:
    local = value.astimezone(EASTERN)
    boundary = datetime.combine(local.date(), time(20, 0), EASTERN)
    return Decimal(str((boundary - local).total_seconds() / 60))


def entry_cutoff_reached(value: datetime, config: SessionManagementConfig) -> bool:
    remaining = minutes_until_overnight(value)
    return Decimal("0") < remaining <= Decimal(config.after_hours_entry_cutoff_minutes)


def flatten_window_reached(value: datetime, config: SessionManagementConfig) -> bool:
    remaining = minutes_until_overnight(value)
    return Decimal("0") < remaining <= Decimal(config.flatten_lead_minutes)


def overnight_session_follows(value: datetime) -> bool:
    local = value.astimezone(EASTERN)
    return is_trading_day(local.date() + timedelta(days=1))


def assess_overnight_carry(
    *, config: SessionManagementConfig, protection_active: bool,
    current_r: Decimal | None, peak_r: Decimal | None,
    giveback_r: Decimal | None, quote_fresh: bool,
    pressure_score: int | None, overnight_available: bool = True,
) -> OvernightCarryAssessment:
    reasons: list[str] = []
    if not config.overnight_carry_enabled:
        reasons.append("OVERNIGHT_NOT_ENABLED")
    if not overnight_available:
        reasons.append("NO_OVERNIGHT_SESSION")
    if not protection_active:
        reasons.append("PROTECTION_NOT_RECONCILED")
    if current_r is None or current_r < config.overnight_minimum_current_r:
        reasons.append("CURRENT_R_TOO_LOW")
    if peak_r is None or peak_r < config.overnight_minimum_peak_r:
        reasons.append("PEAK_R_TOO_LOW")
    if giveback_r is None or giveback_r > config.overnight_maximum_giveback_r:
        reasons.append("GIVEBACK_TOO_LARGE")
    if not quote_fresh:
        reasons.append("MICROSTRUCTURE_STALE")
    if pressure_score is None or pressure_score < config.overnight_minimum_pressure_score:
        reasons.append("PRESSURE_ADVERSE_OR_UNKNOWN")
    return OvernightCarryAssessment(not reasons, tuple(reasons or ("OVERNIGHT_CARRY_APPROVED",)))


__all__ = [
    "OvernightCarryAssessment", "assess_overnight_carry",
    "entry_cutoff_reached", "flatten_window_reached", "minutes_until_overnight",
    "overnight_session_follows",
]
