from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from app.strategy_engine import (
    StrategyDecision,
    StrategyOrderIntent,
)


class CoordinationStage(StrEnum):
    STRATEGY = "STRATEGY"
    INTENT = "INTENT"
    RISK = "RISK"
    COMPLIANCE = "COMPLIANCE"
    EXECUTION = "EXECUTION"
    COMPLETE = "COMPLETE"


class CoordinationStatus(StrEnum):
    SKIPPED = "SKIPPED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"


@dataclass(frozen=True, slots=True)
class CoordinationTrace:
    stage: CoordinationStage
    approved: bool
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("trace message is required")


RiskT = TypeVar("RiskT")
ComplianceT = TypeVar("ComplianceT")
ExecutionT = TypeVar("ExecutionT")
ProposalT = TypeVar("ProposalT")


@dataclass(frozen=True, slots=True)
class ExecutionCoordinationResult(
    Generic[RiskT, ComplianceT, ExecutionT, ProposalT]
):
    status: CoordinationStatus
    final_stage: CoordinationStage
    strategy_decision: StrategyDecision
    order_intent: StrategyOrderIntent | None
    proposal: ProposalT | None
    risk_decision: RiskT | None
    compliance_decision: ComplianceT | None
    execution_result: ExecutionT | None
    trace: tuple[CoordinationTrace, ...]

    @property
    def executed(self) -> bool:
        return self.status is CoordinationStatus.EXECUTED

    @property
    def rejected(self) -> bool:
        return self.status is CoordinationStatus.REJECTED

    @property
    def skipped(self) -> bool:
        return self.status is CoordinationStatus.SKIPPED
