from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from app.backtesting.datasource import frames_fingerprint
from app.backtesting.models import BacktestConfig, HistoricalFrame, canonical_fingerprint
from app.backtesting.runner import run_backtest
from app.experiments.comparison import build_comparison
from app.experiments.models import (
    ExperimentDefinition, ExperimentResult, ExperimentRuntime, ExperimentSuiteResult,
)


def run_experiment(
    frames: tuple[HistoricalFrame, ...], definition: ExperimentDefinition
) -> ExperimentResult:
    _validate_definition(definition)
    config = BacktestConfig(
        account_type=definition.account_type,
        initial_cash=definition.initial_cash,
        compliance_limits=definition.compliance_configuration,
        paper_execution_config=definition.paper_execution_configuration,
        kill_switch=definition.kill_switch,
        settlement_holidays=definition.settlement_holidays,
        warmup_candles=definition.warmup_candles,
        strategy_version=definition.strategy_version,
        prompt_version=definition.prompt_version,
        risk_limits=definition.risk_configuration,
    )
    backtest = run_backtest(frames, definition.ai_responses, definition.order_intents, config)
    duration = backtest.end_timestamp - backtest.start_timestamp
    runtime = ExperimentRuntime(
        (duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds,
        backtest.number_of_candles,
        len(backtest.checkpoint.replay_journal.events),
        len(backtest.checkpoint.paper_journal.events),
    )
    configuration_value = {
        "strategy_version": definition.strategy_version,
        "prompt_version": definition.prompt_version,
        "ai_responses": definition.ai_responses,
        "order_intents": definition.order_intents,
        "risk_configuration": definition.risk_configuration,
        "compliance_configuration": definition.compliance_configuration,
        "paper_execution_configuration": definition.paper_execution_configuration,
        "account_type": definition.account_type,
        "initial_cash": definition.initial_cash,
        "kill_switch": definition.kill_switch,
        "settlement_holidays": definition.settlement_holidays,
        "warmup_candles": definition.warmup_candles,
    }
    return ExperimentResult(
        definition.experiment_id.strip(), backtest, runtime, frames_fingerprint(frames),
        canonical_fingerprint(configuration_value), definition.notes,
    )


def run_experiments(
    frames: tuple[HistoricalFrame, ...], definitions: tuple[ExperimentDefinition, ...]
) -> ExperimentSuiteResult:
    if not definitions:
        raise ValueError("at least one experiment is required")
    normalized = [definition.experiment_id.strip() for definition in definitions]
    if len(set(normalized)) != len(normalized):
        raise ValueError("experiment IDs must be unique")
    results = tuple(run_experiment(frames, definition) for definition in sorted(definitions, key=lambda item: item.experiment_id.strip()))
    dataset = frames_fingerprint(frames)
    if any(result.dataset_fingerprint != dataset for result in results):
        raise ValueError("experiments did not use one identical dataset")
    return ExperimentSuiteResult(dataset, results, build_comparison(results))


def _validate_definition(value: ExperimentDefinition) -> None:
    if not isinstance(value, ExperimentDefinition) or not value.experiment_id.strip():
        raise ValueError("experiment ID is required")
    if not value.strategy_version.strip() or not value.prompt_version.strip():
        raise ValueError("strategy and prompt versions are required")
    if not isinstance(value.initial_cash, Decimal) or not value.initial_cash.is_finite() or value.initial_cash <= 0:
        raise ValueError("initial cash must be a finite positive Decimal")
    risk = value.risk_configuration
    if (
        not isinstance(risk.minimum_confidence, int) or isinstance(risk.minimum_confidence, bool)
        or not 0 <= risk.minimum_confidence <= 100
        or any(not isinstance(item, Decimal) or not item.is_finite() or item <= 0 for item in (
            risk.minimum_reward_risk_ratio, risk.maximum_position_percent,
            risk.missing_atr_position_percent,
        ))
    ):
        raise ValueError("risk configuration is malformed")
    if len({item.candle_timestamp for item in value.ai_responses}) != len(value.ai_responses):
        raise ValueError("AI response timestamps must be unique")
    if len({item.candle_timestamp for item in value.order_intents}) != len(value.order_intents):
        raise ValueError("order-intent timestamps must be unique")
