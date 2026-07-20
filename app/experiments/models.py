from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.backtesting.models import BacktestOrderIntent, SuppliedAIResponse
from app.backtesting.results import BacktestResult
from app.compliance.models import AccountType
from app.order_compliance.kill_switch import KillSwitchState
from app.order_compliance.models import ComplianceLimits
from app.paper_trading.models import PaperExecutionConfig
from app.risk.limits import RiskLimits


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    experiment_id: str
    strategy_version: str
    prompt_version: str
    ai_responses: tuple[SuppliedAIResponse, ...]
    order_intents: tuple[BacktestOrderIntent, ...]
    risk_configuration: RiskLimits
    compliance_configuration: ComplianceLimits
    paper_execution_configuration: PaperExecutionConfig
    account_type: AccountType
    initial_cash: Decimal
    kill_switch: KillSwitchState
    notes: str = ""
    settlement_holidays: frozenset[str] = frozenset()
    warmup_candles: int = 26


@dataclass(frozen=True, slots=True)
class ExperimentRuntime:
    historical_microseconds: int
    candles_processed: int
    replay_events: int
    paper_events: int


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    experiment_id: str
    backtest_result: BacktestResult
    runtime: ExperimentRuntime
    dataset_fingerprint: str
    configuration_fingerprint: str
    notes: str


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    experiment_id: str
    total_return: Decimal
    maximum_drawdown: Decimal
    win_rate: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal | None
    number_of_trades: int
    number_of_rejected_proposals: int
    number_of_gfv_rejections: int
    number_of_compliance_rejections: int
    dataset_fingerprint: str
    configuration_fingerprint: str
    runtime: ExperimentRuntime


@dataclass(frozen=True, slots=True)
class ExperimentSuiteResult:
    dataset_fingerprint: str
    experiment_results: tuple[ExperimentResult, ...]
    comparison_rows: tuple[ComparisonRow, ...]
