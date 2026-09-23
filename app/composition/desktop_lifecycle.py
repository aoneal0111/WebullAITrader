from __future__ import annotations

from time import perf_counter

from app.performance_diagnostics import performance_diagnostics


def _shutdown_stage(name: str, callback):
    started = perf_counter()
    success = False
    try:
        result = callback()
        success = True
        return result
    finally:
        performance_diagnostics.record_component_duration(
            f"shutdown.stage.{name}",
            (perf_counter() - started) * 1000.0,
            event_type="SHUTDOWN",
            success=success,
        )


def close_desktop_composition(
    composition: object,
    *,
    timeout_seconds: float = 5.0,
) -> bool:
    """Close composed resources in lifecycle order without trading authority."""

    symbol_intelligence_stopped = True

    crypto = getattr(composition, "crypto_research_runtime", None)
    if crypto is not None:
        try:
            _shutdown_stage(
                "crypto_research", lambda: crypto.close(timeout_seconds=min(timeout_seconds, 2.0))
            )
        except Exception:
            pass

    acquisition = getattr(
        composition,
        "crypto_catalyst_acquisition_runtime",
        None,
    )
    if acquisition is not None:
        try:
            _shutdown_stage(
                "crypto_acquisition", lambda: acquisition.close(timeout_seconds=min(timeout_seconds, 2.0))
            )
        except Exception:
            pass

    intelligence = getattr(composition, "crypto_intelligence_runtime", None)
    if intelligence is not None:
        try:
            _shutdown_stage(
                "crypto_intelligence", lambda: intelligence.close(timeout_seconds=min(timeout_seconds, 2.0))
            )
        except Exception:
            pass

    diagnostics = getattr(composition, "memory_observability", None)
    if diagnostics is not None and diagnostics.enabled:
        try:
            diagnostics.record_lifecycle("shutdown")
        except Exception:
            pass
        try:
            diagnostics.sample()
        except Exception:
            pass
        try:
            _shutdown_stage(
                "memory_diagnostics", lambda: diagnostics.close(timeout_seconds=min(timeout_seconds, 2.0))
            )
        except Exception:
            pass

    symbol_intelligence = getattr(composition, "symbol_intelligence", None)
    if symbol_intelligence is not None:
        try:
            symbol_intelligence_stopped = _shutdown_stage(
                "symbol_intelligence",
                lambda: symbol_intelligence.close(
                    timeout_seconds=min(timeout_seconds, 5.0)
                ),
            )
        except Exception:
            symbol_intelligence_stopped = False

    runtime_stopped = _shutdown_stage(
        "runtime_service",
        lambda: composition.runtime_service.close(
            timeout_seconds=timeout_seconds
        ),
    )

    paper_validation_capture = getattr(
        composition, "paper_validation_capture", None
    )
    if paper_validation_capture is not None:
        try:
            _shutdown_stage("paper_validation_capture", paper_validation_capture.close)
        except Exception:
            # Validation capture is diagnostic-only and cannot block shutdown.
            pass

    daily_review_exporter = getattr(
        composition,
        "daily_review_exporter",
        None,
    )
    if daily_review_exporter is not None:
        try:
            _shutdown_stage(
                "daily_review_export", lambda: daily_review_exporter.export(composition)
            )
        except Exception:
            # Review export is diagnostic-only and cannot block shutdown.
            pass

    paper_trading_commands = getattr(
        composition,
        "paper_trading_commands",
        None,
    )
    if paper_trading_commands is not None:
        _shutdown_stage("paper_trading_commands", paper_trading_commands.close)

    warrior_forward_sidecar = getattr(
        composition,
        "warrior_forward_sidecar",
        None,
    )
    if warrior_forward_sidecar is not None:
        _shutdown_stage("warrior_sidecar", warrior_forward_sidecar.stop)

    trade_intelligence_observer = getattr(
        composition,
        "trade_intelligence_observer",
        None,
    )
    if trade_intelligence_observer is not None:
        _shutdown_stage(
            "trade_intelligence",
            lambda: trade_intelligence_observer.stop(
                timeout_seconds=timeout_seconds
            ),
        )

    _shutdown_stage("state_store", composition.state_store.close)

    try:
        performance_diagnostics.finish_run()
    except Exception:
        pass

    return runtime_stopped and symbol_intelligence_stopped


__all__ = ["close_desktop_composition"]
