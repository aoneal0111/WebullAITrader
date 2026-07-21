from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from app.strategy_engine import StrategyOrderIntent


ResponseT = TypeVar("ResponseT")
SnapshotT = TypeVar("SnapshotT")
RiskLimitsT = TypeVar("RiskLimitsT")

ProposalT = TypeVar("ProposalT")
AccountT = TypeVar("AccountT")
MarketT = TypeVar("MarketT")
RiskT = TypeVar("RiskT")
GFVT = TypeVar("GFVT")
ComplianceLimitsT = TypeVar("ComplianceLimitsT")
KillSwitchT = TypeVar("KillSwitchT")

ComplianceT = TypeVar("ComplianceT")
PortfolioT = TypeVar("PortfolioT")
QuoteT = TypeVar("QuoteT")
ExecutionConfigT = TypeVar("ExecutionConfigT")
JournalT = TypeVar("JournalT")
EquityCurveT = TypeVar("EquityCurveT")


@dataclass(frozen=True, slots=True)
class RiskEvaluationContext(
    Generic[ResponseT, SnapshotT, RiskLimitsT]
):
    response: ResponseT
    snapshot: SnapshotT
    limits: RiskLimitsT


@dataclass(frozen=True, slots=True)
class ComplianceEvaluationContext(
    Generic[
        ProposalT,
        AccountT,
        MarketT,
        RiskT,
        GFVT,
        ComplianceLimitsT,
        KillSwitchT,
    ]
):
    proposal: ProposalT
    account_state: AccountT
    market_state: MarketT
    risk_decision: RiskT
    gfv_decision: GFVT
    limits: ComplianceLimitsT
    kill_switch: KillSwitchT


@dataclass(frozen=True, slots=True)
class PaperExecutionContext(
    Generic[
        PortfolioT,
        ProposalT,
        ComplianceT,
        QuoteT,
        ExecutionConfigT,
        JournalT,
        EquityCurveT,
    ]
):
    portfolio: PortfolioT
    proposal: ProposalT
    compliance_decision: ComplianceT
    market_quote: QuoteT
    execution_config: ExecutionConfigT
    journal: JournalT
    equity_curve: EquityCurveT


@dataclass(frozen=True, slots=True)
class CoordinationRequest:
    order_intent: StrategyOrderIntent
    advisory_response: object
    snapshot: object
    risk_limits: object
    account_state: object
    market_state: object
    gfv_decision: object
    compliance_limits: object
    kill_switch: object
    portfolio: object
    market_quote: object
    execution_config: object
    journal: object
    equity_curve: object
