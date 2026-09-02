"""Synthetic bounded-state throughput/cardinality benchmark."""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter
import tracemalloc

from .engine import MultiStrategyDiscoveryEngine


def run_cardinality_benchmark(contexts, *, repetitions: int = 1):
    contexts = tuple(contexts)
    if not contexts or repetitions <= 0:
        raise ValueError("benchmark requires contexts and positive repetitions")
    engine = MultiStrategyDiscoveryEngine()
    tracemalloc.start()
    started = perf_counter()
    for _ in range(repetitions):
        for context in contexts:
            engine.observe(context)
    elapsed = perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    metrics = engine.metrics()
    return {
        "market_observations": metrics.market_observations,
        "detector_evaluations": metrics.detector_evaluations,
        "raw_detections": metrics.raw_detector_firings,
        "unique_detector_episodes": metrics.unique_detector_episodes,
        "normalized_opportunities": metrics.normalized_opportunities,
        "average_strategies_per_opportunity": metrics.average_strategies_per_opportunity,
        "maximum_strategies_per_opportunity": metrics.maximum_strategies_per_opportunity,
        "processing_seconds": elapsed,
        "observations_per_second": metrics.market_observations / elapsed,
        "peak_memory_bytes": peak,
        "tracked_symbols": metrics.tracked_symbols,
    }
