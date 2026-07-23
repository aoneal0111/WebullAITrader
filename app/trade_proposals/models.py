from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.risk.models import LegacyRiskDecision, RiskDecision
from app.trade_proposals.policies import (
    ProposalOrderType, TradeProposalPolicy, decimal_value,
)


class TradeDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class ProposalStatus(StrEnum):
    READY = "READY"
    REJECTED = "REJECTED"


class ProposalReasonCode(StrEnum):
    READY = "READY"
    RISK_NOT_APPROVED = "RISK_NOT_APPROVED"
    NON_DIRECTIONAL_COMMITTEE = "NON_DIRECTIONAL_COMMITTEE"
    ZERO_APPROVED_NOTIONAL = "ZERO_APPROVED_NOTIONAL"
    INVALID_REFERENCE_PRICE = "INVALID_REFERENCE_PRICE"
    QUANTITY_BELOW_MINIMUM = "QUANTITY_BELOW_MINIMUM"
    NOTIONAL_BELOW_MINIMUM = "NOTIONAL_BELOW_MINIMUM"
    STOP_DISTANCE_TOO_SMALL = "STOP_DISTANCE_TOO_SMALL"
    STOP_DISTANCE_TOO_LARGE = "STOP_DISTANCE_TOO_LARGE"
    TARGET_DISTANCE_INVALID = "TARGET_DISTANCE_INVALID"
    RISK_REWARD_TOO_LOW = "RISK_REWARD_TOO_LOW"
    PRICE_INCREMENT_INVALID = "PRICE_INCREMENT_INVALID"
    QUANTITY_INCREMENT_INVALID = "QUANTITY_INCREMENT_INVALID"
    MULTIPLE_CONSTRAINTS = "MULTIPLE_CONSTRAINTS"


@dataclass(frozen=True, slots=True)
class TradeProposalRequest:
    risk_decision: RiskDecision
    reference_price: Decimal
    timestamp: datetime
    policy: TradeProposalPolicy
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.risk_decision, LegacyRiskDecision) or not isinstance(
            self.risk_decision, RiskDecision
        ):
            raise ValueError("risk_decision must be a normalized RiskDecision")
        object.__setattr__(self, "reference_price", decimal_value("reference_price", self.reference_price))
        if self.reference_price <= 0:
            raise ValueError("reference_price must be greater than zero")
        object.__setattr__(self, "timestamp", aware_timestamp(self.timestamp))
        if self.timestamp < self.risk_decision.timestamp:
            raise ValueError("request timestamp cannot precede RiskDecision timestamp")
        if not isinstance(self.policy, TradeProposalPolicy):
            raise ValueError("policy must be a TradeProposalPolicy")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_decision": self.risk_decision.to_dict(),
            "reference_price": str(self.reference_price),
            "timestamp": self.timestamp.isoformat(),
            "policy": self.policy.to_dict(),
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TradeProposalRequest:
        if not isinstance(value, Mapping):
            raise ValueError("serialized request must be a mapping")
        try:
            return cls(
                RiskDecision.from_dict(value["risk_decision"]),
                value["reference_price"], datetime.fromisoformat(value["timestamp"]),
                TradeProposalPolicy.from_dict(value["policy"]), value.get("metadata", {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize trade proposal request") from exc


@dataclass(frozen=True, slots=True)
class TradeProposalCheck:
    code: ProposalReasonCode
    passed: bool
    observed: Decimal | int | float | str
    limit: Decimal | int | float | str | None
    message: str
    blocking: bool

    def __post_init__(self) -> None:
        if not isinstance(self.code, ProposalReasonCode):
            raise ValueError("code must be a ProposalReasonCode")
        if not isinstance(self.passed, bool) or not isinstance(self.blocking, bool):
            raise ValueError("passed and blocking must be booleans")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be nonempty")
        for name in ("observed", "limit"):
            value = getattr(self, name)
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError(f"{name} must be finite")
            if isinstance(value, float) and (value != value or abs(value) == float("inf")):
                raise ValueError(f"{name} must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "passed": self.passed,
                "observed": json_number(self.observed), "limit": json_number(self.limit),
                "message": self.message, "blocking": self.blocking}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TradeProposalCheck:
        if not isinstance(value, Mapping):
            raise ValueError("serialized check must be a mapping")
        try:
            code = ProposalReasonCode(value["code"])
            observed, limit = value["observed"], value.get("limit")
            decimal_codes = set(ProposalReasonCode) - {
                ProposalReasonCode.RISK_NOT_APPROVED,
                ProposalReasonCode.NON_DIRECTIONAL_COMMITTEE,
            }
            if code in decimal_codes and observed != "not configured":
                observed = Decimal(observed)
                limit = Decimal(limit) if limit is not None else None
            return cls(code, value["passed"], observed, limit, value["message"], value["blocking"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize trade proposal check") from exc


@dataclass(frozen=True, slots=True)
class TradeProposal:
    proposal_id: str
    symbol: str
    timestamp: datetime
    status: ProposalStatus
    direction: TradeDirection | None
    order_type: ProposalOrderType | None
    approved_notional: Decimal
    quantity: Decimal
    reference_price: Decimal
    proposed_entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    per_unit_risk: Decimal
    total_planned_risk: Decimal
    expected_reward: Decimal
    risk_reward_ratio: Decimal
    primary_reason: ProposalReasonCode
    checks: tuple[TradeProposalCheck, ...]
    reasons: tuple[str, ...]
    policy_version: str
    proposal_engine_version: str
    risk_policy_version: str
    risk_committee_version: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not self.proposal_id.strip():
            raise ValueError("proposal_id must be nonempty")
        symbol = self.symbol.strip() if isinstance(self.symbol, str) else ""
        if not symbol or symbol != symbol.upper():
            raise ValueError("symbol must be nonempty and uppercase")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timestamp", aware_timestamp(self.timestamp))
        if not isinstance(self.status, ProposalStatus): raise ValueError("status must be a ProposalStatus")
        if self.direction is not None and not isinstance(self.direction, TradeDirection): raise ValueError("direction must be a TradeDirection or None")
        if self.order_type is not None and not isinstance(self.order_type, ProposalOrderType): raise ValueError("order_type must be a ProposalOrderType or None")
        decimal_names = ("approved_notional", "quantity", "reference_price", "proposed_entry_price",
                         "stop_loss_price", "take_profit_price", "per_unit_risk", "total_planned_risk",
                         "expected_reward", "risk_reward_ratio")
        for name in decimal_names:
            value = decimal_value(name, getattr(self, name))
            if value < 0: raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, value)
        if self.status is ProposalStatus.REJECTED and (self.approved_notional != 0 or self.quantity != 0 or self.total_planned_risk != 0):
            raise ValueError("REJECTED proposals must authorize zero notional, quantity, and planned risk")
        if self.status is ProposalStatus.READY and (self.direction is None or self.order_type is None or self.quantity <= 0 or
            min(self.proposed_entry_price, self.stop_loss_price, self.take_profit_price) <= 0):
            raise ValueError("READY proposals require direction, order type, quantity, and positive prices")
        if not isinstance(self.primary_reason, ProposalReasonCode): raise ValueError("primary_reason must be a ProposalReasonCode")
        if not isinstance(self.checks, tuple) or not self.checks or not all(isinstance(x, TradeProposalCheck) for x in self.checks):
            raise ValueError("checks must be a nonempty tuple of TradeProposalCheck")
        if not isinstance(self.reasons, tuple) or not self.reasons or any(not isinstance(x, str) or not x.strip() for x in self.reasons):
            raise ValueError("reasons must be a nonempty tuple of strings")
        for name in ("policy_version", "proposal_engine_version", "risk_policy_version", "risk_committee_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip(): raise ValueError(f"{name} must be nonempty")
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        result = {name: str(getattr(self, name)) for name in (
            "approved_notional", "quantity", "reference_price", "proposed_entry_price",
            "stop_loss_price", "take_profit_price", "per_unit_risk", "total_planned_risk",
            "expected_reward", "risk_reward_ratio")}
        result.update({"proposal_id": self.proposal_id, "symbol": self.symbol, "timestamp": self.timestamp.isoformat(),
                       "status": self.status.value, "direction": self.direction.value if self.direction else None,
                       "order_type": self.order_type.value if self.order_type else None, "primary_reason": self.primary_reason.value,
                       "checks": [x.to_dict() for x in self.checks], "reasons": list(self.reasons),
                       "policy_version": self.policy_version, "proposal_engine_version": self.proposal_engine_version,
                       "risk_policy_version": self.risk_policy_version, "risk_committee_version": self.risk_committee_version,
                       "metadata": thaw_json_value(self.metadata)})
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TradeProposal:
        if not isinstance(value, Mapping): raise ValueError("serialized proposal must be a mapping")
        try:
            data = dict(value)
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
            data["status"] = ProposalStatus(data["status"])
            data["direction"] = TradeDirection(data["direction"]) if data["direction"] else None
            data["order_type"] = ProposalOrderType(data["order_type"]) if data["order_type"] else None
            data["primary_reason"] = ProposalReasonCode(data["primary_reason"])
            data["checks"] = tuple(TradeProposalCheck.from_dict(x) for x in data["checks"])
            data["reasons"] = tuple(data["reasons"])
            return cls(**data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize trade proposal") from exc


def aware_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def json_number(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value
