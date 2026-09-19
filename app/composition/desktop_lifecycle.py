from __future__ import annotations

from app.performance_diagnostics import performance_diagnostics


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
            crypto.close(timeout_seconds=min(timeout_seconds, 2.0))
        except Exception:
            pass

    acquisition = getattr(
        composition,
        "crypto_catalyst_acquisition_runtime",
        None,
    )
    if acquisition is not None:
        try:
            acquisition.close(timeout_seconds=min(timeout_seconds, 2.0))
        except Exception:
            pass

    intelligence = getattr(composition, "crypto_intelligence_runtime", None)
    if intelligence is not None:
        try:
            intelligence.close(timeout_seconds=min(timeout_seconds, 2.0))
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
            diagnostics.close(timeout_seconds=min(timeout_seconds, 2.0))
        except Exception:
            pass

    symbol_intelligence = getattr(composition, "symbol_intelligence", None)
    if symbol_intelligence is not None:
        try:
            symbol_intelligence_stopped = symbol_intelligence.close(
                timeout_seconds=min(timeout_seconds, 5.0)
            )
        except Exception:
            symbol_intelligence_stopped = False

    runtime_stopped = composition.runtime_service.close(
        timeout_seconds=timeout_seconds
    )

    daily_review_exporter = getattr(
        composition,
        "daily_review_exporter",
        None,
    )
    if daily_review_exporter is not None:
        try:
            daily_review_exporter.export(composition)
        except Exception:
            # Review export is diagnostic-only and cannot block shutdown.
            pass

    paper_trading_commands = getattr(
        composition,
        "paper_trading_commands",
        None,
    )
    if paper_trading_commands is not None:
        paper_trading_commands.close()

    warrior_forward_sidecar = getattr(
        composition,
        "warrior_forward_sidecar",
        None,
    )
    if warrior_forward_sidecar is not None:
        warrior_forward_sidecar.stop()

    trade_intelligence_observer = getattr(
        composition,
        "trade_intelligence_observer",
        None,
    )
    if trade_intelligence_observer is not None:
        trade_intelligence_observer.stop(
            timeout_seconds=timeout_seconds
        )

    composition.state_store.close()

    try:
        performance_diagnostics.finish_run()
    except Exception:
        pass

    return runtime_stopped and symbol_intelligence_stopped


__all__ = ["close_desktop_composition"]
