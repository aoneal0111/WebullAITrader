from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.execution.models import ExecutionResult
from app.outcomes.policies import OutcomePolicy
from app.trade_proposals.models import TradeDirection, aware_timestamp
from app.trade_proposals.policies import decimal_value


class OutcomeStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class OutcomeCheck:
    name: str
    passed: bool

    def __post_init__(self) -> None:
        if self.name not in {"execution filled", "exit price positive", "quantity positive"}:
            raise ValueError("name must be a recognized outcome check")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OutcomeCheck:
        if not isinstance(value, Mapping):
            raise ValueError("serialized check must be a mapping")
        try:
            return cls(value["name"], value["passed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize outcome check") from exc


@dataclass(frozen=True, slots=True)
class OutcomeRequest:
    execution_result: ExecutionResult
    exit_price: Decimal
    timestamp: datetime
    policy: OutcomePolicy
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.execution_result, ExecutionResult):
            raise ValueError("execution_result must be an ExecutionResult")
        exit_price = decimal_value("exit_price", self.exit_price)
        if exit_price <= 0:
            raise ValueError("exit_price must be greater than zero")
        object.__setattr__(self, "exit_price", exit_price)
        object.__setattr__(self, "timestamp", aware_timestamp(self.timestamp))
        if self.timestamp < self.execution_result.timestamp:
            raise ValueError("outcome timestamp cannot precede execution timestamp")
        if not isinstance(self.policy, OutcomePolicy):
            raise ValueError("policy must be an OutcomePolicy")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"execution_result": self.execution_result.to_dict(), "exit_price": str(self.exit_price),
                "timestamp": self.timestamp.isoformat(), "policy": self.policy.to_dict(),
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OutcomeRequest:
        if not isinstance(value, Mapping):
            raise ValueError("serialized request must be a mapping")
        try:
            return cls(ExecutionResult.from_dict(value["execution_result"]), value["exit_price"],
                       datetime.fromisoformat(value["timestamp"]), OutcomePolicy.from_dict(value["policy"]),
                       value.get("metadata", {}))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize outcome request") from exc


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    outcome_id: str
    execution_id: str
    proposal_id: str
    symbol: str
    direction: TradeDirection
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    commission: Decimal
    slippage: Decimal
    gross_cost: Decimal
    net_cost: Decimal
    realized_pnl: Decimal
    realized_return: Decimal
    timestamp: datetime
    status: OutcomeStatus
    policy_version: str
    execution_engine_version: str
    proposal_engine_version: str
    risk_policy_version: str
    risk_committee_version: str
    outcome_engine_version: str
    checks: tuple[OutcomeCheck, ...]
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("outcome_id", "execution_id", "proposal_id", "policy_version",
                     "execution_engine_version", "proposal_engine_version", "risk_policy_version",
                     "risk_committee_version", "outcome_engine_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty")
            object.__setattr__(self, name, value.strip())
        symbol = self.symbol.strip() if isinstance(self.symbol, str) else ""
        if not symbol or symbol != symbol.upper():
            raise ValueError("symbol must be nonempty and uppercase")
        object.__setattr__(self, "symbol", symbol)
        if not isinstance(self.direction, TradeDirection):
            raise ValueError("direction must be a TradeDirection")
        for name in ("quantity", "entry_price", "exit_price", "commission", "slippage", "gross_cost", "net_cost",
                     "realized_pnl", "realized_return"):
            object.__setattr__(self, name, decimal_value(name, getattr(self, name)))
        if self.quantity <= 0 or self.entry_price <= 0 or self.exit_price <= 0:
            raise ValueError("quantity, entry_price, and exit_price must be positive")
        if self.commission < 0 or self.slippage < 0 or self.gross_cost <= 0 or self.net_cost < 0:
            raise ValueError("costs must be nonnegative and gross_cost must be positive")
        object.__setattr__(self, "timestamp", aware_timestamp(self.timestamp))
        if not isinstance(self.status, OutcomeStatus):
            raise ValueError("status must be an OutcomeStatus")
        expected = ("execution filled", "exit price positive", "quantity positive")
        if (not isinstance(self.checks, tuple) or
                not all(isinstance(x, OutcomeCheck) for x in self.checks) or
                (self.checks and tuple(x.name for x in self.checks) != expected)):
            raise ValueError("checks must be empty or contain the stable ordered outcome checks")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        data = {name: str(getattr(self, name)) for name in ("quantity", "entry_price", "exit_price", "commission",
            "slippage", "gross_cost", "net_cost", "realized_pnl", "realized_return")}
        data.update({"outcome_id": self.outcome_id, "execution_id": self.execution_id,
            "proposal_id": self.proposal_id, "symbol": self.symbol, "direction": self.direction.value,
            "timestamp": self.timestamp.isoformat(), "status": self.status.value, "policy_version": self.policy_version,
            "execution_engine_version": self.execution_engine_version,
            "proposal_engine_version": self.proposal_engine_version, "risk_policy_version": self.risk_policy_version,
            "risk_committee_version": self.risk_committee_version, "outcome_engine_version": self.outcome_engine_version,
            "checks": [x.to_dict() for x in self.checks], "metadata": thaw_json_value(self.metadata)})
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TradeOutcome:
        if not isinstance(value, Mapping):
            raise ValueError("serialized outcome must be a mapping")
        try:
            data = dict(value)
            data["direction"] = TradeDirection(data["direction"])
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
            data["status"] = OutcomeStatus(data["status"])
            data["checks"] = tuple(OutcomeCheck.from_dict(x) for x in data["checks"])
            return cls(**data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize trade outcome") from exc
