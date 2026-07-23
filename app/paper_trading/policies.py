from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.paper_trading.exceptions import PaperTradingValidationError


def _nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise PaperTradingValidationError(f"{name} must be Decimal-compatible")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PaperTradingValidationError(f"{name} must be finite") from exc
    if not result.is_finite() or result < 0:
        raise PaperTradingValidationError(f"{name} must be non-negative and finite")
    return result


@dataclass(frozen=True, slots=True)
class PaperTradingPolicy:
    version: str = "paper_trading_policy_v1"
    enabled: bool = False
    allow_partial_fills: bool = False
    commission_per_order: Decimal = Decimal("0")
    commission_per_share: Decimal = Decimal("0")
    slippage_basis_points: Decimal = Decimal("0")
    reject_insufficient_cash: bool = True
    reject_oversell: bool = True
    strict_validation: bool = True
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip() or self.version != self.version.strip():
            raise PaperTradingValidationError("version must be a non-empty stripped string")
        for name in ("enabled", "allow_partial_fills", "reject_insufficient_cash", "reject_oversell", "strict_validation"):
            if not isinstance(getattr(self, name), bool):
                raise PaperTradingValidationError("policy flags must be boolean")
        for name in ("commission_per_order", "commission_per_share", "slippage_basis_points"):
            object.__setattr__(self, name, _nonnegative(getattr(self, name), name))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"version": self.version, "enabled": self.enabled, "allow_partial_fills": self.allow_partial_fills,
                "commission_per_order": str(self.commission_per_order), "commission_per_share": str(self.commission_per_share),
                "slippage_basis_points": str(self.slippage_basis_points), "reject_insufficient_cash": self.reject_insufficient_cash,
                "reject_oversell": self.reject_oversell, "strict_validation": self.strict_validation,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            return cls(**dict(value))
        except PaperTradingValidationError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise PaperTradingValidationError("invalid paper trading policy") from exc
