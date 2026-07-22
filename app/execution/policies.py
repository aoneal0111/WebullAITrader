from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.trade_proposals.policies import decimal_value


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    version: str = "execution_policy_v1"
    commission_per_share: Decimal = Decimal("0")
    minimum_commission: Decimal = Decimal("0")
    slippage_per_share: Decimal = Decimal("0")
    allow_partial_fills: bool = False
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        object.__setattr__(self, "version", self.version.strip())
        for name in ("commission_per_share", "minimum_commission", "slippage_per_share"):
            value = decimal_value(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, value)
        if not isinstance(self.allow_partial_fills, bool):
            raise ValueError("allow_partial_fills must be a boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "commission_per_share": str(self.commission_per_share),
            "minimum_commission": str(self.minimum_commission),
            "slippage_per_share": str(self.slippage_per_share),
            "allow_partial_fills": self.allow_partial_fills,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExecutionPolicy:
        if not isinstance(value, Mapping):
            raise ValueError("serialized policy must be a mapping")
        try:
            return cls(**dict(value))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize execution policy") from exc
