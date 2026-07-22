from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.execution_orchestrator.exceptions import ExecutionOrchestratorValidationError
from app.execution_planner import ExecutionPlanResult
from app.paper_trading import PaperExecutionResult, PaperTradingAccount
from app.portfolio import PortfolioSnapshot
from app.risk import RiskResult
from app.strategy import StrategyResult


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ExecutionOrchestratorValidationError(f"{name} must be a non-empty stripped string")
    return value


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise ExecutionOrchestratorValidationError(f"{name} must be Decimal-compatible")
    try: result = Decimal(value)
    except (InvalidOperation, ValueError) as exc: raise ExecutionOrchestratorValidationError(f"{name} must be finite") from exc
    if not result.is_finite() or result <= 0: raise ExecutionOrchestratorValidationError(f"{name} must be positive and finite")
    return result


class PaperTradingCycleOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    NO_ACTION = "NO_ACTION"
    STRATEGY_REJECTED = "STRATEGY_REJECTED"
    RISK_REJECTED = "RISK_REJECTED"
    PLANNING_REJECTED = "PLANNING_REJECTED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    DISABLED = "DISABLED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PaperTradingCycleRequest:
    request_id: str
    account_id: str
    portfolio: PortfolioSnapshot
    paper_account: PaperTradingAccount
    market_price: Decimal
    execution_timestamp: datetime
    requested_quantity: Decimal
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id")); object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        if not isinstance(self.portfolio, PortfolioSnapshot): raise ExecutionOrchestratorValidationError("portfolio must be PortfolioSnapshot")
        if not isinstance(self.paper_account, PaperTradingAccount): raise ExecutionOrchestratorValidationError("paper_account must be PaperTradingAccount")
        if self.portfolio.account_id != self.account_id or self.paper_account.account_id != self.account_id:
            raise ExecutionOrchestratorValidationError("request account identity mismatch")
        object.__setattr__(self, "market_price", _positive(self.market_price, "market_price"))
        object.__setattr__(self, "requested_quantity", _positive(self.requested_quantity, "requested_quantity"))
        if not isinstance(self.execution_timestamp, datetime) or self.execution_timestamp.tzinfo is None or self.execution_timestamp.utcoffset() is None:
            raise ExecutionOrchestratorValidationError("execution_timestamp must be timezone-aware")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"request_id": self.request_id, "account_id": self.account_id, "portfolio": self.portfolio.to_dict(),
                "paper_account": self.paper_account.to_dict(), "market_price": str(self.market_price),
                "execution_timestamp": self.execution_timestamp.isoformat(), "requested_quantity": str(self.requested_quantity),
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["portfolio"] = PortfolioSnapshot.from_dict(data["portfolio"])
            data["paper_account"] = PaperTradingAccount.from_dict(data["paper_account"])
            data["execution_timestamp"] = datetime.fromisoformat(data["execution_timestamp"]); return cls(**data)
        except ExecutionOrchestratorValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise ExecutionOrchestratorValidationError("invalid cycle request") from exc


@dataclass(frozen=True, slots=True)
class PaperTradingCycleCriteriaResult:
    stage: str
    passed: bool
    detail: str

    def __post_init__(self):
        object.__setattr__(self, "stage", _text(self.stage, "stage")); object.__setattr__(self, "detail", _text(self.detail, "detail"))
        if not isinstance(self.passed, bool): raise ExecutionOrchestratorValidationError("criteria passed must be boolean")

    def to_dict(self): return {"stage": self.stage, "passed": self.passed, "detail": self.detail}
    @classmethod
    def from_dict(cls, value): return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class PaperTradingCycleResult:
    request_id: str
    account_id: str
    outcome: PaperTradingCycleOutcome
    strategy_result: StrategyResult | None
    risk_result: RiskResult | None
    execution_plan_result: ExecutionPlanResult | None
    paper_execution_result: PaperExecutionResult | None
    resulting_account: PaperTradingAccount
    criteria_results: tuple[PaperTradingCycleCriteriaResult, ...]
    policy_version: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id")); object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version"))
        if not isinstance(self.outcome, PaperTradingCycleOutcome): raise ExecutionOrchestratorValidationError("outcome must be PaperTradingCycleOutcome")
        expected = (("strategy_result", StrategyResult), ("risk_result", RiskResult), ("execution_plan_result", ExecutionPlanResult), ("paper_execution_result", PaperExecutionResult))
        if any(getattr(self, name) is not None and not isinstance(getattr(self, name), kind) for name, kind in expected): raise ExecutionOrchestratorValidationError("stage result type is invalid")
        if not isinstance(self.resulting_account, PaperTradingAccount) or self.resulting_account.account_id != self.account_id: raise ExecutionOrchestratorValidationError("resulting account identity mismatch")
        if self.paper_execution_result is not None and self.resulting_account != self.paper_execution_result.account: raise ExecutionOrchestratorValidationError("resulting account must match paper execution")
        if not isinstance(self.criteria_results, tuple) or any(not isinstance(x, PaperTradingCycleCriteriaResult) for x in self.criteria_results): raise ExecutionOrchestratorValidationError("criteria_results must be immutable")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"request_id": self.request_id, "account_id": self.account_id, "outcome": self.outcome.value,
                "strategy_result": self.strategy_result.to_dict() if self.strategy_result else None,
                "risk_result": self.risk_result.to_dict() if self.risk_result else None,
                "execution_plan_result": self.execution_plan_result.to_dict() if self.execution_plan_result else None,
                "paper_execution_result": self.paper_execution_result.to_dict() if self.paper_execution_result else None,
                "resulting_account": self.resulting_account.to_dict(), "criteria_results": [x.to_dict() for x in self.criteria_results],
                "policy_version": self.policy_version, "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["outcome"] = PaperTradingCycleOutcome(data["outcome"])
            data["strategy_result"] = StrategyResult.from_dict(data["strategy_result"]) if data.get("strategy_result") else None
            data["risk_result"] = RiskResult.from_dict(data["risk_result"]) if data.get("risk_result") else None
            data["execution_plan_result"] = ExecutionPlanResult.from_dict(data["execution_plan_result"]) if data.get("execution_plan_result") else None
            data["paper_execution_result"] = PaperExecutionResult.from_dict(data["paper_execution_result"]) if data.get("paper_execution_result") else None
            data["resulting_account"] = PaperTradingAccount.from_dict(data["resulting_account"])
            data["criteria_results"] = tuple(PaperTradingCycleCriteriaResult.from_dict(x) for x in data["criteria_results"]); return cls(**data)
        except ExecutionOrchestratorValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise ExecutionOrchestratorValidationError("invalid cycle result") from exc
