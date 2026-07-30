from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")

    if value != value.strip():
        raise ValueError(f"{field_name} must be stripped")

    return value


def _optional_text(
    value: str | None,
    field_name: str,
) -> str | None:
    if value is None:
        return None

    return _required_text(value, field_name)


@dataclass(frozen=True, slots=True)
class PositionReadModel:
    """Immutable consumer-facing representation of one operational position."""

    account_id: str
    symbol: str
    asset_type: str
    quantity: str
    average_cost: str
    market_value: str | None
    unrealized_gain_loss: str | None
    realized_gain_loss: str | None
    currency: str
    updated_at: datetime
    exposure: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "account_id",
            "symbol",
            "asset_type",
            "quantity",
            "average_cost",
            "currency",
        ):
            _required_text(
                getattr(self, field_name),
                field_name,
            )

        _optional_text(
            self.market_value,
            "market_value",
        )
        _optional_text(
            self.unrealized_gain_loss,
            "unrealized_gain_loss",
        )
        _optional_text(
            self.realized_gain_loss,
            "realized_gain_loss",
        )
        _optional_text(
            self.exposure,
            "exposure",
        )

        if not isinstance(self.updated_at, datetime):
            raise TypeError("updated_at must be a datetime")

        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class PositionsReadModelSnapshot:
    """Immutable collection delivered to presentation consumers."""

    positions: tuple[PositionReadModel, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.positions, tuple):
            raise TypeError("positions must be an immutable tuple")

        if any(
            not isinstance(position, PositionReadModel)
            for position in self.positions
        ):
            raise TypeError(
                "positions must contain only PositionReadModel instances"
            )

    @classmethod
    def initial(cls) -> "PositionsReadModelSnapshot":
        return cls()
