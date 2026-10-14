"""Inert PAPER-only outcome collector composition; activation belongs to GUI launch."""
from app.asset_modules.learning_collector import LearningOutcomeCollector, source_paths
from app.composition.runtime_mode import RuntimeMode


def create_learning_collector(configuration, runtime_mode, *, paper_persistence_path):
    if not configuration.learning_outcome_collection_enabled:
        return None
    if (configuration.environment.value != "PAPER" or configuration.live_trading_enabled
            or runtime_mode is not RuntimeMode.PAPER or paper_persistence_path is None):
        raise ValueError("Outcome collection requires PAPER mode and a durable paper ledger")
    return LearningOutcomeCollector(configuration.learning_outcome_journal_path,
        source_paths(paper_persistence_path, configuration.execution_database_path),
        interval_seconds=configuration.learning_outcome_collection_seconds)
