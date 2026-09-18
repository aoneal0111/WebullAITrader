from __future__ import annotations

from collections.abc import Callable

from app.memory_observability import MemoryObservability
from app.performance_diagnostics import performance_diagnostics


def optional_metrics(root: object | None, *attributes: str) -> dict[str, int]:
    """Resolve a live diagnostic owner without creating or retaining one."""

    current = root
    for attribute in attributes:
        current = getattr(current, attribute, None)
        if current is None:
            return {}
    provider = getattr(current, "memory_metrics", None)
    return {} if not callable(provider) else dict(provider())


def create_desktop_memory_observability(
    *,
    operational_configuration: object,
    warrior_forward_sidecar: object,
    runtime_driver_metrics: Callable[..., dict[str, int]],
    trade_intelligence_observer: object,
    adaptive_entry_research_observer: object,
    crypto_research_runtime: object,
    paper_order_book: object,
    runtime_projections: object,
    state_store: object,
    bus: object,
    observability_factory: Callable[..., object] = MemoryObservability,
) -> object:
    """Build diagnostic-only memory telemetry outside desktop trading composition."""

    return observability_factory(
        {
            "warrior_forward_runtime": lambda: optional_metrics(
                warrior_forward_sidecar, "_service"
            ),
            "warrior_forward_report": lambda: optional_metrics(
                warrior_forward_sidecar, "_report_worker"
            ),
            "websocket_callback_queue": lambda: runtime_driver_metrics(
                "_market_data"
            ),
            "trade_intelligence_runtime": (
                trade_intelligence_observer.memory_metrics
            ),
            "trade_intelligence_discovery_worker": lambda: optional_metrics(
                trade_intelligence_observer, "_service", "_discovery_worker"
            ),
            "multi_strategy_discovery_engine": lambda: optional_metrics(
                trade_intelligence_observer,
                "_service",
                "_discovery_worker",
                "engine",
            ),
            "realtime_scanner": lambda: runtime_driver_metrics(
                "_scanner", "_engine"
            ),
            "scanner_snapshot_publisher": lambda: runtime_driver_metrics(
                "_scanner_publisher"
            ),
            "adaptive_entry_runtime": (
                adaptive_entry_research_observer.memory_metrics
            ),
            "adaptive_entry_worker": lambda: optional_metrics(
                adaptive_entry_research_observer, "_worker"
            ),
            "crypto_research": crypto_research_runtime.memory_metrics,
            "paper_order_book": paper_order_book.memory_metrics,
            "order_projection": runtime_projections.order_projection.memory_metrics,
            "position_projection": (
                runtime_projections.position_projection.memory_metrics
            ),
            "decision_projection": (
                runtime_projections.decision_projection.memory_metrics
            ),
            "health_projection": (
                runtime_projections.health_projection.memory_metrics
            ),
            "watchlist_projection": (
                runtime_projections.watchlist_projection.memory_metrics
            ),
            "timeline_projection": (
                runtime_projections.timeline_projection.memory_metrics
            ),
            "application_state": state_store.memory_metrics,
            "operations_bus": bus.memory_metrics,
            "startup": performance_diagnostics.startup_metrics,
        },
        enabled=operational_configuration.memory_observability_enabled,
        path=operational_configuration.memory_observability_path,
        interval_seconds=(
            operational_configuration.memory_observability_interval_seconds
        ),
        tracemalloc_enabled=(
            operational_configuration.memory_tracemalloc_enabled
        ),
        tracemalloc_snapshot_interval_seconds=(
            operational_configuration.memory_tracemalloc_snapshot_interval_seconds
        ),
        gc_tracked_objects_enabled=(
            operational_configuration.memory_gc_tracked_objects_enabled
        ),
    )


__all__ = [
    "create_desktop_memory_observability",
    "optional_metrics",
]
