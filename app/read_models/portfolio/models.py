from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PortfolioReadModelSnapshot:
    """Immutable portfolio summary projected from authoritative application state."""

    timestamp: datetime | None = None
    session_id: str | None = None
    equity: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    current_drawdown: Decimal = Decimal("0")
    total_return: Decimal = Decimal("0")
    maximum_drawdown: Decimal = Decimal("0")
    win_rate: Decimal = Decimal("0")
    order_count: int = 0
    position_count: int = 0

    def __post_init__(self) -> None:
        if self.timestamp is not None:
            if not isinstance(self.timestamp, datetime):
                raise TypeError("timestamp must be a datetime or None")
            if self.timestamp.tzinfo is None:
                raise ValueError("timestamp must be timezone-aware")
        if self.session_id is not None:
            if not isinstance(self.session_id, str):
                raise TypeError("session_id must be a string or None")
            if not self.session_id.strip():
                raise ValueError("session_id must not be empty")
            if self.session_id != self.session_id.strip():
                raise ValueError("session_id must be stripped")
        for field_name in (
            "equity", "peak_equity", "realized_pnl", "unrealized_pnl",
            "current_drawdown", "total_return", "maximum_drawdown", "win_rate",
        ):
            if not isinstance(getattr(self, field_name), Decimal):
                raise TypeError(f"{field_name} must be a Decimal")
        for field_name in ("order_count", "position_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must be nonnegative")

    @classmethod
    def initial(cls) -> "PortfolioReadModelSnapshot":
        return cls()
