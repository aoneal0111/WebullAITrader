"""Autonomous, observational Trade Intelligence Memory.

This package intentionally exports no broker, order, account, authorization, or
production-policy capability.  Its values are research records, never signals.
"""

from .analogs import AnalogQuery, AnalogResult, HistoricalAnalogEngine
from .experience_store import ExperienceStore
from .models import (
    ActualPaperExecutionOutcome, AtlasDecision,
    DatasetPartition,
    DecisionTimeSnapshot,
    ExperienceSource,
    HorizonOutcome,
    MissedOpportunityClassification,
    OpportunityKey,
    OutcomeKind, OutcomeStatus,
    PriceBar,
    ResearchGeneration,
    ResearchGenerationCompletion,
    TradeOpportunityExperience,
    WorkerMetrics,
)
from .outcome_engine import OutcomeEngine, classify_missed_opportunity
from .reporting import ExperienceReporter
from .service import DEFAULT_STORE_PATH, TradeIntelligenceService

__all__ = [
    "ActualPaperExecutionOutcome", "AnalogQuery", "AnalogResult", "AtlasDecision", "DatasetPartition",
    "DecisionTimeSnapshot", "DEFAULT_STORE_PATH", "ExperienceReporter", "ExperienceSource",
    "ExperienceStore", "HistoricalAnalogEngine", "HorizonOutcome",
    "MissedOpportunityClassification", "OpportunityKey",
    "OutcomeEngine", "OutcomeKind", "OutcomeStatus", "PriceBar", "TradeIntelligenceService",
    "ResearchGeneration", "ResearchGenerationCompletion", "TradeOpportunityExperience",
    "WorkerMetrics", "classify_missed_opportunity",
]
