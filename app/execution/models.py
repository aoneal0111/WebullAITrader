from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.execution.policies import ExecutionPolicy
from app.trade_proposals.models import TradeDirection, TradeProposal, aware_timestamp
from app.trade_proposals.policies import decimal_value


class ExecutionStatus(StrEnum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"


class ExecutionReason(StrEnum):
    FILLED = "FILLED"
    PROPOSAL_NOT_READY = "PROPOSAL_NOT_READY"
    ZERO_QUANTITY = "ZERO_QUANTITY"
    INVALID_ENTRY_PRICE = "INVALID_ENTRY_PRICE"


@dataclass(frozen=True, slots=True)
class ExecutionCheck:
    name: str
    passed: bool

    def __post_init__(self) -> None:
        if self.name not in {"proposal ready", "quantity positive", "entry positive"}:
            raise ValueError("name must be a recognized execution check")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExecutionCheck:
        if not isinstance(value, Mapping):
            raise ValueError("serialized check must be a mapping")
        try:
            return cls(value["name"], value["passed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize execution check") from exc


@dataclass(frozen=True, slots=True)
class PaperExecutionRequest:
    proposal: TradeProposal
    timestamp: datetime
    policy: ExecutionPolicy
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, TradeProposal):
            raise ValueError("proposal must be a TradeProposal")
        object.__setattr__(self, "timestamp", aware_timestamp(self.timestamp))
        if self.timestamp < self.proposal.timestamp:
            raise ValueError("execution timestamp cannot precede proposal timestamp")
        if not isinstance(self.policy, ExecutionPolicy):
            raise ValueError("policy must be an ExecutionPolicy")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"proposal": self.proposal.to_dict(), "timestamp": self.timestamp.isoformat(),
                "policy": self.policy.to_dict(), "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PaperExecutionRequest:
        if not isinstance(value, Mapping):
            raise ValueError("serialized request must be a mapping")
        try:
            return cls(TradeProposal.from_dict(value["proposal"]), datetime.fromisoformat(value["timestamp"]),
                       ExecutionPolicy.from_dict(value["policy"]), value.get("metadata", {}))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize paper execution request") from exc


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    execution_id: str
    proposal_id: str
    symbol: str
    timestamp: datetime
    status: ExecutionStatus
    reason: ExecutionReason
    direction: TradeDirection | None
    quantity: Decimal
    filled_quantity: Decimal
    requested_entry_price: Decimal
    fill_price: Decimal
    commission: Decimal
    slippage: Decimal
    gross_value: Decimal
    net_cost: Decimal
    policy_version: str
    execution_engine_version: str
    proposal_engine_version: str
    risk_policy_version: str
    risk_committee_version: str
    checks: tuple[ExecutionCheck, ...]
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("execution_id", "proposal_id", "policy_version", "execution_engine_version",
                     "proposal_engine_version", "risk_policy_version", "risk_committee_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty")
            object.__setattr__(self, name, value.strip())
        symbol = self.symbol.strip() if isinstance(self.symbol, str) else ""
        if not symbol or symbol != symbol.upper():
            raise ValueError("symbol must be nonempty and uppercase")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timestamp", aware_timestamp(self.timestamp))
        if not isinstance(self.status, ExecutionStatus) or not isinstance(self.reason, ExecutionReason):
            raise ValueError("status and reason must be execution enums")
        if self.direction is not None and not isinstance(self.direction, TradeDirection):
            raise ValueError("direction must be a TradeDirection or None")
        for name in ("quantity", "filled_quantity", "requested_entry_price", "fill_price", "commission",
                     "slippage", "gross_value", "net_cost"):
            value = decimal_value(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, value)
        if self.status is ExecutionStatus.FILLED:
            if self.reason is not ExecutionReason.FILLED or self.direction is None or self.quantity <= 0:
                raise ValueError("FILLED results require a direction, positive quantity, and FILLED reason")
            if self.filled_quantity != self.quantity or self.fill_price <= 0:
                raise ValueError("FILLED results require full quantity and positive fill price")
        elif self.reason is ExecutionReason.FILLED or self.filled_quantity != 0:
            raise ValueError("REJECTED results require a rejection reason and zero filled quantity")
        expected_names = ("proposal ready", "quantity positive", "entry positive")
        if (not isinstance(self.checks, tuple) or tuple(x.name for x in self.checks) != expected_names
                or not all(isinstance(x, ExecutionCheck) for x in self.checks)):
            raise ValueError("checks must contain the stable ordered execution checks")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        data = {name: str(getattr(self, name)) for name in ("quantity", "filled_quantity",
            "requested_entry_price", "fill_price", "commission", "slippage", "gross_value", "net_cost")}
        data.update({"execution_id": self.execution_id, "proposal_id": self.proposal_id, "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(), "status": self.status.value, "reason": self.reason.value,
            "direction": self.direction.value if self.direction else None, "policy_version": self.policy_version,
            "execution_engine_version": self.execution_engine_version,
            "proposal_engine_version": self.proposal_engine_version, "risk_policy_version": self.risk_policy_version,
            "risk_committee_version": self.risk_committee_version, "checks": [x.to_dict() for x in self.checks],
            "metadata": thaw_json_value(self.metadata)})
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExecutionResult:
        if not isinstance(value, Mapping):
            raise ValueError("serialized result must be a mapping")
        try:
            data = dict(value)
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
            data["status"] = ExecutionStatus(data["status"])
            data["reason"] = ExecutionReason(data["reason"])
            data["direction"] = TradeDirection(data["direction"]) if data["direction"] else None
            data["checks"] = tuple(ExecutionCheck.from_dict(x) for x in data["checks"])
            return cls(**data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize execution result") from exc
