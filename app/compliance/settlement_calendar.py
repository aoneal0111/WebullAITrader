from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class SettlementCalendar:
    """Offline market-business-day calendar with injectable holidays."""

    holidays: frozenset[date] = field(default_factory=frozenset)

    def is_market_business_day(self, value: date) -> bool:
        return value.weekday() < 5 and value not in self.holidays

    def add_market_business_days(self, start: date, days: int) -> date:
        if days < 0:
            raise ValueError("days must be non-negative")
        current = start
        remaining = days
        while remaining:
            current += timedelta(days=1)
            if self.is_market_business_day(current):
                remaining -= 1
        return current

    def settlement_date(self, trade_date: date, settlement_days: int = 1) -> date:
        """Calculate T+n by market business days, never elapsed hours."""
        return self.add_market_business_days(trade_date, settlement_days)
