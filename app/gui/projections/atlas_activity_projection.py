from __future__ import annotations

from app.gui.models import AtlasActivityRow, AtlasActivitySnapshot
from app.operations_core import ApplicationState


def project_atlas_activity(state: ApplicationState) -> AtlasActivitySnapshot:
    """Project existing operational facts without estimating missing values."""

    health = state.health_projection
    runtime = state.runtime
    candidate_count = sum(
        1
        for entry in state.watchlist_projection.entries
        if dict(entry.metadata).get("scanner_rank") is not None
    )
    candidates = (
        str(candidate_count)
        if candidate_count or any(
            dict(entry.metadata).get("scanner_rank") is not None
            for entry in state.watchlist_projection.entries
        )
        else "Unknown"
    )
    values = (
        ("Universe", health.supported_symbols),
        ("Evaluating", None),
        ("Candidates", candidates),
        ("Open Positions", state.portfolio_projection.open_positions),
        ("Pending Orders", state.portfolio_projection.working_orders),
        ("Market Data", health.market_data_status or runtime.market_feed_status),
        ("Broker", health.broker_status or runtime.broker_status),
    )
    return AtlasActivitySnapshot(
        rows=tuple(
            AtlasActivityRow(
                label=label,
                value=_display(value),
                tone=_tone(value),
            )
            for label, value in values
        )
    )


def _display(value: object | None) -> str:
    if value is None or value == "" or value == "--":
        return "Unknown"
    if isinstance(value, str):
        return value.replace("_", " ").title()
    return str(value)


def _tone(value: object | None) -> str:
    normalized = str(value or "").upper()
    if any(
        word in normalized
        for word in ("FAILED", "ERROR", "DISCONNECTED", "UNAVAILABLE")
    ):
        return "danger"
    if any(
        word in normalized
        for word in ("PAUSED", "STARTING", "DEGRADED", "STOPPING")
    ):
        return "warn"
    if any(
        word in normalized
        for word in ("RUNNING", "CONNECTED", "HEALTHY", "READY")
    ):
        return "good"
    return "neutral"


__all__ = ["project_atlas_activity"]
