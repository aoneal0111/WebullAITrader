from __future__ import annotations

from app.services import TradingService


def finalize_desktop_runtime(
    *,
    runtime_service_factory: object,
    bus: object,
    driver_factory: object,
    runtime_mode: object,
    event_sink: object,
    market_event_observer: object,
    sec_shadow_runtime: object,
    placement_runtime: object,
    cancellation_runtime: object,
    runtime_driver_metrics: object,
    symbol_intelligence: object | None,
    memory_observability: object,
) -> tuple[object, TradingService]:
    """Finalize runtime construction after all optional composition is ready."""

    try:
        runtime_service = runtime_service_factory(
            bus,
            driver_factory=driver_factory,
            runtime_mode=runtime_mode,
            event_sink=event_sink,
            market_event_observer=market_event_observer,
            sec_shadow_evaluator=sec_shadow_runtime.evaluator,
        )
        runtime_driver_metrics.bind(runtime_service)
        trading_service = TradingService(
            placement_runtime,
            cancellation_runtime,
        )
    except Exception as error:
        if symbol_intelligence is not None:
            try:
                cleanup_complete = symbol_intelligence.close(
                    timeout_seconds=5.0
                )
            except Exception:
                cleanup_complete = False
            if not cleanup_complete:
                setattr(
                    error,
                    "symbol_intelligence_holder",
                    symbol_intelligence,
                )
        raise

    if memory_observability.enabled:
        memory_observability.record_lifecycle("startup")
        memory_observability.start()

    return runtime_service, trading_service


__all__ = ["finalize_desktop_runtime"]
