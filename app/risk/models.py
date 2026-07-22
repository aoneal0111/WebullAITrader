from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping

from app.committee.models import (
    AgentOpinionAction,
    CommitteeAction,
    CommitteeOpinion,
    CommitteeVote,
    JSONValue,
    freeze_json_mapping,
    thaw_json_value,
)


class RiskDecisionAction(StrEnum):
    APPROVE = "APPROVE"
    MODIFY = "MODIFY"
    REJECT = "REJECT"


class RiskReasonCode(StrEnum):
    APPROVED = "APPROVED"
    NEUTRAL_COMMITTEE = "NEUTRAL_COMMITTEE"
    ZERO_REQUESTED_NOTIONAL = "ZERO_REQUESTED_NOTIONAL"
    INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"
    SYMBOL_EXPOSURE_LIMIT = "SYMBOL_EXPOSURE_LIMIT"
    GROSS_EXPOSURE_LIMIT = "GROSS_EXPOSURE_LIMIT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    OPEN_POSITION_LIMIT = "OPEN_POSITION_LIMIT"
    OPEN_ORDER_LIMIT = "OPEN_ORDER_LIMIT"
    REQUESTED_RISK_LIMIT = "REQUESTED_RISK_LIMIT"
    COMMITTEE_CONFIDENCE_TOO_LOW = "COMMITTEE_CONFIDENCE_TOO_LOW"
    COMMITTEE_CONSENSUS_TOO_LOW = "COMMITTEE_CONSENSUS_TOO_LOW"
    MULTIPLE_LIMITS = "MULTIPLE_LIMITS"


@dataclass(frozen=True, slots=True)
class RiskState:
    symbol: str
    timestamp: datetime
    account_equity: Decimal
    available_buying_power: Decimal
    current_symbol_exposure: Decimal
    total_gross_exposure: Decimal
    daily_realized_pnl: Decimal
    daily_unrealized_pnl: Decimal
    current_drawdown_fraction: Decimal
    open_positions: int
    open_orders: int
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        for name in (
            "account_equity", "available_buying_power",
            "current_symbol_exposure", "total_gross_exposure",
            "daily_realized_pnl", "daily_unrealized_pnl",
            "current_drawdown_fraction",
        ):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))
        if self.account_equity <= 0:
            raise ValueError("account_equity must be greater than zero")
        for name in ("available_buying_power", "current_symbol_exposure", "total_gross_exposure"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.current_symbol_exposure > self.total_gross_exposure:
            raise ValueError("current_symbol_exposure cannot exceed total_gross_exposure")
        if not Decimal(0) <= self.current_drawdown_fraction <= Decimal(1):
            raise ValueError("current_drawdown_fraction must be between 0 and 1")
        for name in ("open_positions", "open_orders"):
            _nonnegative_int(name, getattr(self, name))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "timestamp": self.timestamp.isoformat(),
            **{name: str(getattr(self, name)) for name in (
                "account_equity", "available_buying_power", "current_symbol_exposure",
                "total_gross_exposure", "daily_realized_pnl", "daily_unrealized_pnl",
                "current_drawdown_fraction")},
            "open_positions": self.open_positions, "open_orders": self.open_orders,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RiskState:
        if not isinstance(value, Mapping):
            raise ValueError("serialized risk state must be a mapping")
        try:
            return cls(**{**value, "timestamp": datetime.fromisoformat(value["timestamp"])})
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize risk state") from exc


@dataclass(frozen=True, slots=True)
class RiskEvaluationRequest:
    committee_opinion: CommitteeOpinion
    risk_state: RiskState
    requested_notional: Decimal
    requested_risk_fraction: Decimal
    timestamp: datetime
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.committee_opinion, CommitteeOpinion):
            raise ValueError("committee_opinion must be a CommitteeOpinion")
        if not isinstance(self.risk_state, RiskState):
            raise ValueError("risk_state must be a RiskState")
        if self.committee_opinion.symbol != self.risk_state.symbol:
            raise ValueError("committee and risk-state symbols must match")
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        if self.committee_opinion.timestamp > self.timestamp:
            raise ValueError("committee timestamp cannot be after request timestamp")
        if self.risk_state.timestamp > self.timestamp:
            raise ValueError("risk-state timestamp cannot be after request timestamp")
        object.__setattr__(self, "requested_notional", _decimal("requested_notional", self.requested_notional))
        object.__setattr__(self, "requested_risk_fraction", _decimal("requested_risk_fraction", self.requested_risk_fraction))
        if self.requested_notional < 0:
            raise ValueError("requested_notional must be nonnegative")
        if not Decimal(0) <= self.requested_risk_fraction <= Decimal(1):
            raise ValueError("requested_risk_fraction must be between 0 and 1")
        if self.committee_opinion.action is CommitteeAction.NEUTRAL and (
            self.requested_notional != 0 or self.requested_risk_fraction != 0
        ):
            raise ValueError("NEUTRAL committee opinions require zero notional and risk")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"committee_opinion": self.committee_opinion.to_dict(), "risk_state": self.risk_state.to_dict(),
                "requested_notional": str(self.requested_notional), "requested_risk_fraction": str(self.requested_risk_fraction),
                "timestamp": self.timestamp.isoformat(), "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RiskEvaluationRequest:
        if not isinstance(value, Mapping): raise ValueError("serialized request must be a mapping")
        try:
            return cls(_committee_opinion_from_dict(value["committee_opinion"]), RiskState.from_dict(value["risk_state"]),
                       value["requested_notional"], value["requested_risk_fraction"],
                       datetime.fromisoformat(value["timestamp"]), value.get("metadata", {}))
        except (KeyError, TypeError, ValueError) as exc: raise ValueError("Unable to deserialize risk request") from exc


@dataclass(frozen=True, slots=True)
class RiskLimitCheck:
    code: RiskReasonCode
    passed: bool
    observed: Decimal | int | float | str
    limit: Decimal | int | float | str | None
    message: str
    blocking: bool

    def __post_init__(self) -> None:
        if not isinstance(self.code, RiskReasonCode): raise ValueError("code must be a RiskReasonCode")
        if not isinstance(self.passed, bool) or not isinstance(self.blocking, bool): raise ValueError("passed and blocking must be booleans")
        if not isinstance(self.message, str) or not self.message.strip(): raise ValueError("message must be nonempty")
        for name in ("observed", "limit"):
            value = getattr(self, name)
            if isinstance(value, float) and (value != value or abs(value) == float("inf")): raise ValueError(f"{name} must be finite")
            if isinstance(value, Decimal) and not value.is_finite(): raise ValueError(f"{name} must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "passed": self.passed, "observed": _json_number(self.observed),
                "limit": _json_number(self.limit), "message": self.message, "blocking": self.blocking}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RiskLimitCheck:
        if not isinstance(value, Mapping): raise ValueError("serialized check must be a mapping")
        try:
            code = RiskReasonCode(value["code"])
            observed, limit = value["observed"], value.get("limit")
            if code not in {RiskReasonCode.NEUTRAL_COMMITTEE, RiskReasonCode.OPEN_POSITION_LIMIT, RiskReasonCode.OPEN_ORDER_LIMIT}:
                observed = Decimal(observed)
                limit = Decimal(limit) if limit is not None else None
            return cls(code, value["passed"], observed, limit, value["message"], value["blocking"])
        except (KeyError, TypeError, ValueError) as exc: raise ValueError("Unable to deserialize risk check") from exc


@dataclass(frozen=True, slots=True)
class RiskDecision:
    symbol: str
    timestamp: datetime
    action: RiskDecisionAction
    approved_notional: Decimal
    approved_risk_fraction: Decimal
    committee_action: CommitteeAction
    committee_confidence: float
    committee_consensus: float
    primary_reason: RiskReasonCode
    checks: tuple[RiskLimitCheck, ...]
    reasons: tuple[str, ...]
    policy_version: str
    committee_version: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        if len(args) == 7 and not kwargs:
            return LegacyRiskDecision(*args)
        return object.__new__(cls)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol)); object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        if not isinstance(self.action, RiskDecisionAction): raise ValueError("action must be a RiskDecisionAction")
        if not isinstance(self.committee_action, CommitteeAction): raise ValueError("committee_action must be a CommitteeAction")
        object.__setattr__(self, "approved_notional", _decimal("approved_notional", self.approved_notional))
        object.__setattr__(self, "approved_risk_fraction", _decimal("approved_risk_fraction", self.approved_risk_fraction))
        if self.approved_notional < 0 or not Decimal(0) <= self.approved_risk_fraction <= Decimal(1): raise ValueError("approved values are out of bounds")
        for name in ("committee_confidence", "committee_consensus"):
            value = float(getattr(self, name))
            if isinstance(getattr(self, name), bool) or not 0 <= value <= 1: raise ValueError(f"{name} must be between 0 and 1")
            object.__setattr__(self, name, value)
        if not isinstance(self.primary_reason, RiskReasonCode): raise ValueError("primary_reason must be a RiskReasonCode")
        if not isinstance(self.checks, tuple) or not self.checks or not all(isinstance(x, RiskLimitCheck) for x in self.checks): raise ValueError("checks must be a nonempty tuple of RiskLimitCheck")
        if not isinstance(self.reasons, tuple) or not self.reasons or any(not isinstance(x, str) or not x.strip() for x in self.reasons): raise ValueError("reasons must be a nonempty tuple of strings")
        if self.action is RiskDecisionAction.REJECT and (self.approved_notional != 0 or self.approved_risk_fraction != 0): raise ValueError("REJECT decisions must approve zero")
        for name in ("policy_version", "committee_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip(): raise ValueError(f"{name} must be nonempty")
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "timestamp": self.timestamp.isoformat(), "action": self.action.value,
                "approved_notional": str(self.approved_notional), "approved_risk_fraction": str(self.approved_risk_fraction),
                "committee_action": self.committee_action.value, "committee_confidence": self.committee_confidence,
                "committee_consensus": self.committee_consensus, "primary_reason": self.primary_reason.value,
                "checks": [x.to_dict() for x in self.checks], "reasons": list(self.reasons),
                "policy_version": self.policy_version, "committee_version": self.committee_version,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RiskDecision:
        if not isinstance(value, Mapping): raise ValueError("serialized decision must be a mapping")
        try:
            return cls(symbol=value["symbol"], timestamp=datetime.fromisoformat(value["timestamp"]), action=RiskDecisionAction(value["action"]),
                       approved_notional=value["approved_notional"], approved_risk_fraction=value["approved_risk_fraction"],
                       committee_action=CommitteeAction(value["committee_action"]), committee_confidence=value["committee_confidence"],
                       committee_consensus=value["committee_consensus"], primary_reason=RiskReasonCode(value["primary_reason"]),
                       checks=tuple(RiskLimitCheck.from_dict(x) for x in value["checks"]), reasons=tuple(value["reasons"]),
                       policy_version=value["policy_version"], committee_version=value["committee_version"], metadata=value.get("metadata", {}))
        except (KeyError, TypeError, ValueError) as exc: raise ValueError("Unable to deserialize risk decision") from exc


@dataclass(frozen=True, slots=True)
class LegacyRiskDecision:
    """Compatibility model for the pre-committee advisory validator."""
    approved: bool; approval_reason: str; risk_score: int; max_position_percent: Decimal
    stop_loss_valid: bool; take_profit_valid: bool; warnings: tuple[str, ...]
    def __post_init__(self) -> None:
        if not 0 <= self.risk_score <= 100: raise ValueError("risk_score must be between 0 and 100")
        object.__setattr__(self, "max_position_percent", Decimal(str(self.max_position_percent)))
        if not 0 <= self.max_position_percent <= 100: raise ValueError("max_position_percent must be between 0 and 100")
        if not self.approval_reason.strip(): raise ValueError("approval_reason must not be empty")
    def to_dict(self) -> dict[str, Any]:
        return {"approved": self.approved, "approval_reason": self.approval_reason, "risk_score": self.risk_score,
                "max_position_percent": format(self.max_position_percent, "f"), "stop_loss_valid": self.stop_loss_valid,
                "take_profit_valid": self.take_profit_valid, "warnings": list(self.warnings)}


def _symbol(value: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ValueError("symbol must be nonempty")
    normalized = value.strip()
    if normalized != normalized.upper(): raise ValueError("symbol must be uppercase")
    return normalized

def _timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None: raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)

def _decimal(name: str, value: Any) -> Decimal:
    if isinstance(value, bool): raise ValueError(f"{name} must be numeric, not boolean")
    try: result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc: raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite(): raise ValueError(f"{name} must be finite")
    return result

def _nonnegative_int(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0: raise ValueError(f"{name} must be a nonnegative integer")

def _json_number(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value

def _committee_opinion_from_dict(value: Mapping[str, Any]) -> CommitteeOpinion:
    votes = tuple(CommitteeVote(
        agent_name=item["agent_name"], action=AgentOpinionAction(item["action"]), raw_score=item["raw_score"],
        confidence=item["confidence"], configured_weight=item["configured_weight"], effective_weight=item["effective_weight"],
        weighted_score=item["weighted_score"], included=item["included"], exclusion_reason=item["exclusion_reason"])
        for item in value["votes"])
    return CommitteeOpinion(value["symbol"], datetime.fromisoformat(value["timestamp"]), CommitteeAction(value["action"]),
        value["confidence"], value["score"], value["consensus"], value["participating_agents"], value["bullish_agents"],
        value["bearish_agents"], value["neutral_agents"], tuple(value["agent_names"]), tuple(value["reasons"]), votes,
        value["weighting_version"], value["chair_version"], value.get("metadata", {}))
