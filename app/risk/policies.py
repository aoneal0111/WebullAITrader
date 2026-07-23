from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.risk.models import _decimal, _nonnegative_int


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    version: str = "risk_policy_v1"
    maximum_symbol_exposure_fraction: Decimal = Decimal("0.10")
    maximum_gross_exposure_fraction: Decimal = Decimal("0.50")
    maximum_daily_loss_fraction: Decimal = Decimal("0.03")
    maximum_drawdown_fraction: Decimal = Decimal("0.10")
    maximum_requested_risk_fraction: Decimal = Decimal("0.01")
    minimum_committee_confidence: Decimal = Decimal("0.20")
    minimum_committee_consensus: Decimal = Decimal("0.50")
    maximum_open_positions: int = 10
    maximum_open_orders: int = 10
    allow_modification: bool = True
    enabled: bool = False
    strict_validation: bool = True
    max_position_value: Decimal = Decimal("10000")
    max_portfolio_exposure: Decimal = Decimal("50000")
    max_order_quantity: Decimal = Decimal("1000")
    minimum_cash_reserve: Decimal = Decimal("0")
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip(): raise ValueError("version must be nonempty")
        object.__setattr__(self, "version", self.version.strip())
        for name in ("maximum_symbol_exposure_fraction", "maximum_gross_exposure_fraction", "maximum_daily_loss_fraction",
                     "maximum_drawdown_fraction", "maximum_requested_risk_fraction", "minimum_committee_confidence", "minimum_committee_consensus"):
            value = _decimal(name, getattr(self, name))
            if not Decimal(0) <= value <= Decimal(1): raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)
        _nonnegative_int("maximum_open_positions", self.maximum_open_positions)
        _nonnegative_int("maximum_open_orders", self.maximum_open_orders)
        if not isinstance(self.allow_modification, bool): raise ValueError("allow_modification must be a boolean")
        if not isinstance(self.enabled, bool) or not isinstance(self.strict_validation, bool): raise ValueError("runtime policy flags must be booleans")
        for name in ("max_position_value", "max_portfolio_exposure", "max_order_quantity", "minimum_cash_reserve"):
            value = _decimal(name, getattr(self, name))
            if value < 0: raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version,
                **{name: str(getattr(self, name)) for name in (
                    "maximum_symbol_exposure_fraction", "maximum_gross_exposure_fraction", "maximum_daily_loss_fraction",
                    "maximum_drawdown_fraction", "maximum_requested_risk_fraction", "minimum_committee_confidence", "minimum_committee_consensus")},
                "maximum_open_positions": self.maximum_open_positions, "maximum_open_orders": self.maximum_open_orders,
                "allow_modification": self.allow_modification, "enabled": self.enabled,
                "strict_validation": self.strict_validation,
                "max_position_value": str(self.max_position_value),
                "max_portfolio_exposure": str(self.max_portfolio_exposure),
                "max_order_quantity": str(self.max_order_quantity),
                "minimum_cash_reserve": str(self.minimum_cash_reserve),
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RiskPolicy:
        if not isinstance(value, Mapping): raise ValueError("serialized policy must be a mapping")
        try: return cls(**value)
        except (TypeError, ValueError) as exc: raise ValueError("Unable to deserialize risk policy") from exc
