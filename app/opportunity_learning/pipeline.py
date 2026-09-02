"""Offline generation lifecycle through frozen challenger evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from app.trade_intelligence.models import DatasetPartition

from .challengers import (
    CalibratedScoreChallenger, EmpiricalCohortChallenger,
    HistoricalAnalogChallenger, SimpleLogisticChallenger,
)
from .contracts import LearningGeneration, LearningTarget
from .dataset import build_learning_examples
from .dataset import assess_sufficiency
from .evaluation import select_on_validation
from .reporting import build_research_report


@dataclass(frozen=True, slots=True)
class OfflineLearningResult:
    generation_id: str
    examples: tuple
    challengers: tuple
    selected_challenger_ids: tuple[str, ...]
    report: dict
    maximum_conclusion: str


class OfflineLearningPipeline:
    """Automates Phase 2A steps 3-9 and deliberately has no runtime hook."""

    def run(self, generation: LearningGeneration, experiences, outcomes, decisions=()) -> OfflineLearningResult:
        examples = build_learning_examples(tuple(experiences), tuple(outcomes), tuple(decisions), generation=generation.base)
        train = tuple(item for item in examples if item.partition is DatasetPartition.TRAIN)
        validation = tuple(item for item in examples if item.partition is DatasetPartition.VALIDATION)
        challengers = []
        for target in (LearningTarget.ONE_R_BEFORE_STOP, LearningTarget.TWO_R_BEFORE_STOP):
            analog = HistoricalAnalogChallenger(train, target, generation.research_policy)
            empirical = EmpiricalCohortChallenger(train, target, generation.research_policy)
            logistic = SimpleLogisticChallenger.fit(examples, target, generation.research_policy)
            calibrated = CalibratedScoreChallenger.fit(logistic, validation)
            challengers.extend((analog, empirical, logistic, calibrated))
        selected = []
        for target in (LearningTarget.ONE_R_BEFORE_STOP, LearningTarget.TWO_R_BEFORE_STOP):
            candidate = select_on_validation(tuple(item for item in challengers if item.target is target), validation)
            if candidate is not None:
                selected.append(candidate.challenger_id)
        report = build_research_report(
            examples, tuple(challengers), generation.research_policy,
            selected_challenger_ids=tuple(selected),
        )
        sufficient = all(
            assess_sufficiency(examples, target, generation.research_policy).status.value == "SUFFICIENT"
            for target in (LearningTarget.ONE_R_BEFORE_STOP, LearningTarget.TWO_R_BEFORE_STOP)
        )
        return OfflineLearningResult(
            generation.generation_id, examples, tuple(challengers), tuple(selected), report,
            "CHALLENGER_ELIGIBLE_FOR_FRESH_SHADOW" if sufficient and selected else "INSUFFICIENT_EVIDENCE",
        )
