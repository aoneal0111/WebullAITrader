from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from app.redaction import redact_account_number


@dataclass(frozen=True, slots=True)
class Balance:
    total_value: Decimal | None = None
    cash: Decimal | None = None
    buying_power: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    quantity: Decimal
    market_value: Decimal | None = None
    average_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: str
    account_number: str
    balance: Balance
    positions: tuple[Position, ...]

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "account": redact_account_number(self.account_number or self.account_id),
            "balance": _json_safe(asdict(self.balance)),
            "positions": [_json_safe(asdict(position)) for position in self.positions],
            "position_count": len(self.positions),
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value
