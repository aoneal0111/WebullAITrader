from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


class ProposalOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass(frozen=True, slots=True)
class TradeProposalPolicy:
    version: str = "trade_proposal_policy_v1"
    order_type: ProposalOrderType = ProposalOrderType.MARKET
    limit_price_offset_fraction: Decimal = Decimal("0")
    stop_loss_fraction: Decimal = Decimal("0.02")
    take_profit_fraction: Decimal = Decimal("0.04")
    minimum_risk_reward_ratio: Decimal = Decimal("1.50")
    minimum_notional: Decimal = Decimal("1")
    minimum_quantity: Decimal = Decimal("0.0001")
    quantity_increment: Decimal = Decimal("0.0001")
    price_increment: Decimal = Decimal("0.01")
    allow_fractional_quantity: bool = True
    maximum_quantity: Decimal | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        object.__setattr__(self, "version", self.version.strip())
        if not isinstance(self.order_type, ProposalOrderType):
            raise ValueError("order_type must be a ProposalOrderType")
        names = (
            "limit_price_offset_fraction", "stop_loss_fraction",
            "take_profit_fraction", "minimum_risk_reward_ratio",
            "minimum_notional", "minimum_quantity", "quantity_increment",
            "price_increment",
        )
        for name in names:
            object.__setattr__(self, name, decimal_value(name, getattr(self, name)))
        if self.limit_price_offset_fraction < 0:
            raise ValueError("limit_price_offset_fraction must be nonnegative")
        if not Decimal("0") < self.stop_loss_fraction < Decimal("1"):
            raise ValueError("stop_loss_fraction must be greater than zero and less than one")
        if self.take_profit_fraction <= 0:
            raise ValueError("take_profit_fraction must be greater than zero")
        if self.minimum_risk_reward_ratio <= 0:
            raise ValueError("minimum_risk_reward_ratio must be greater than zero")
        if self.minimum_notional < 0:
            raise ValueError("minimum_notional must be nonnegative")
        for name in ("minimum_quantity", "quantity_increment", "price_increment"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.maximum_quantity is not None:
            maximum = decimal_value("maximum_quantity", self.maximum_quantity)
            if maximum <= 0:
                raise ValueError("maximum_quantity must be greater than zero")
            object.__setattr__(self, "maximum_quantity", maximum)
        if not isinstance(self.allow_fractional_quantity, bool):
            raise ValueError("allow_fractional_quantity must be a boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "order_type": self.order_type.value,
            **{name: str(getattr(self, name)) for name in (
                "limit_price_offset_fraction", "stop_loss_fraction",
                "take_profit_fraction", "minimum_risk_reward_ratio",
                "minimum_notional", "minimum_quantity", "quantity_increment",
                "price_increment",
            )},
            "allow_fractional_quantity": self.allow_fractional_quantity,
            "maximum_quantity": (
                str(self.maximum_quantity) if self.maximum_quantity is not None else None
            ),
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TradeProposalPolicy:
        if not isinstance(value, Mapping):
            raise ValueError("serialized policy must be a mapping")
        try:
            return cls(**{**value, "order_type": ProposalOrderType(value["order_type"])})
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize trade proposal policy") from exc


def decimal_value(name: str, value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result
