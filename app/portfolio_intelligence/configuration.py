from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Mapping
import os


@dataclass(frozen=True, slots=True)
class PortfolioIntelligenceConfiguration:
    correlation_lookback: int = 60
    correlation_interval: str = "1d"
    minimum_correlation_observations: int = 20
    high_correlation_threshold: Decimal = Decimal("0.80")
    concentration_warning_threshold: Decimal = Decimal("0.50")
    concentration_critical_threshold: Decimal = Decimal("0.75")
    risk_budget_warning_percentage: Decimal = Decimal("0.80")
    performance_reporting_timezone: str = "America/Chicago"
    trading_day_boundary_hour: int = 0

    def __post_init__(self) -> None:
        if self.correlation_lookback < 2:
            raise ValueError("correlation_lookback must be at least 2")
        if not self.correlation_interval.strip():
            raise ValueError("correlation_interval is required")
        if not 2 <= self.minimum_correlation_observations <= self.correlation_lookback:
            raise ValueError("minimum correlation observations must be within lookback")
        for name in ("high_correlation_threshold", "concentration_warning_threshold", "concentration_critical_threshold", "risk_budget_warning_percentage"):
            value = Decimal(getattr(self, name))
            if not value.is_finite() or not Decimal("0") < value <= Decimal("1"):
                raise ValueError(f"{name} must be in (0, 1]")
            object.__setattr__(self, name, value)
        if self.concentration_warning_threshold > self.concentration_critical_threshold:
            raise ValueError("concentration warning cannot exceed critical threshold")
        if not 0 <= self.trading_day_boundary_hour <= 23:
            raise ValueError("trading_day_boundary_hour must be between 0 and 23")
        try:
            ZoneInfo(self.performance_reporting_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("performance_reporting_timezone is unknown") from exc


@dataclass(frozen=True, slots=True)
class PortfolioRiskLimits:
    maximum_gross_exposure: Decimal | None = None
    maximum_net_exposure: Decimal | None = None
    maximum_largest_position: Decimal | None = None
    maximum_daily_loss: Decimal | None = None
    maximum_drawdown: Decimal | None = None
    maximum_open_positions: int | None = None
    maximum_buying_power_utilization: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("maximum_gross_exposure", "maximum_net_exposure", "maximum_largest_position", "maximum_daily_loss", "maximum_drawdown", "maximum_buying_power_utilization"):
            value = getattr(self, name)
            if value is not None:
                value = Decimal(value)
                if not value.is_finite() or value <= 0:
                    raise ValueError(f"{name} must be positive")
                object.__setattr__(self, name, value)
        if self.maximum_open_positions is not None and (isinstance(self.maximum_open_positions, bool) or self.maximum_open_positions <= 0):
            raise ValueError("maximum_open_positions must be positive")


def load_portfolio_intelligence_configuration(
    values: Mapping[str, str] | None = None,
) -> PortfolioIntelligenceConfiguration:
    """Load broker-independent settings from an explicit environment mapping."""
    source = dict(os.environ) if values is None else dict(values)
    try:
        return PortfolioIntelligenceConfiguration(
            correlation_lookback=int(source.get("PORTFOLIO_CORRELATION_LOOKBACK", "60")),
            correlation_interval=source.get("PORTFOLIO_CORRELATION_INTERVAL", "1d"),
            minimum_correlation_observations=int(source.get("PORTFOLIO_MINIMUM_CORRELATION_OBSERVATIONS", "20")),
            high_correlation_threshold=Decimal(source.get("PORTFOLIO_HIGH_CORRELATION_THRESHOLD", "0.80")),
            concentration_warning_threshold=Decimal(source.get("PORTFOLIO_CONCENTRATION_WARNING_THRESHOLD", "0.50")),
            concentration_critical_threshold=Decimal(source.get("PORTFOLIO_CONCENTRATION_CRITICAL_THRESHOLD", "0.75")),
            risk_budget_warning_percentage=Decimal(source.get("PORTFOLIO_RISK_BUDGET_WARNING_PERCENTAGE", "0.80")),
            performance_reporting_timezone=source.get("PORTFOLIO_PERFORMANCE_TIMEZONE", "America/Chicago"),
            trading_day_boundary_hour=int(source.get("PORTFOLIO_TRADING_DAY_BOUNDARY_HOUR", "0")),
        )
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise ValueError("invalid portfolio intelligence configuration") from exc
