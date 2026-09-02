"""Deterministic offline scaling benchmark for Phase 2A research operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from time import perf_counter
import tracemalloc

from app.trade_intelligence.models import DatasetPartition

from .challengers import HistoricalAnalogChallenger, SimpleLogisticChallenger
from .contracts import LearningTarget, ResearchEvidencePolicy
from .reporting import build_research_report


def run_scaling_benchmark(seed_example, sizes=(10_000, 50_000, 100_000)):
    """Measure bounded research operations without duplicating persisted data."""

    results = []
    policy = ResearchEvidencePolicy(
        minimum_total=2, minimum_train=1, minimum_validation=1, minimum_holdout=1,
        minimum_positive=1, minimum_negative=1, minimum_unique_dates=1,
        minimum_unique_symbols=1, minimum_unique_sessions=1, minimum_cohort=20,
        minimum_analogs=20, examples_per_fitted_feature=10,
    )
    for size in sizes:
        tracemalloc.start()
        started = perf_counter()
        examples = _scaled_examples(seed_example, size)
        extraction_seconds = perf_counter() - started

        started = perf_counter()
        report = build_research_report(examples, (), policy)
        report_seconds = perf_counter() - started

        started = perf_counter()
        analog = HistoricalAnalogChallenger(examples, LearningTarget.ONE_R_BEFORE_STOP, policy)
        analog.predict(examples[-1].features)
        analog_seconds = perf_counter() - started

        started = perf_counter()
        model = SimpleLogisticChallenger.fit(
            examples, LearningTarget.ONE_R_BEFORE_STOP, policy, iterations=25,
        )
        training_seconds = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results.append({
            "experiences": size,
            "feature_extraction_seconds": extraction_seconds,
            "feature_vectors_per_second": size / max(extraction_seconds, 1e-12),
            "cohort_and_report_seconds": report_seconds,
            "analog_query_seconds": analog_seconds,
            "baseline_training_seconds": training_seconds,
            "peak_memory_bytes": peak,
            "report_sections": len(report),
            "fitted_feature_count": 0 if model.encoder is None else len(model.encoder.feature_names),
        })
    return tuple(results)


def _scaled_examples(seed, size):
    result = []
    partitions = (DatasetPartition.TRAIN, DatasetPartition.TRAIN,
                  DatasetPartition.VALIDATION, DatasetPartition.HOLDOUT)
    for index in range(size):
        feature = replace(
            seed.features, experience_id=f"benchmark-exp-{index}",
            decision_id=f"benchmark-decision-{index}", symbol=f"S{index % 100:03d}",
            session_date=date(2020, 1, 1) + timedelta(days=index % 60),
        )
        labels = replace(
            seed.labels, one_r_before_stop=index % 2 == 0,
            two_r_before_stop=index % 4 == 0, three_r_before_stop=index % 8 == 0,
            stop_before_one_r=index % 2 == 1,
            expected_return_r=1.0 if index % 2 == 0 else -1.0,
        )
        result.append(replace(seed, features=feature, labels=labels, partition=partitions[index % 4]))
    return tuple(result)
