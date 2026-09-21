from __future__ import annotations

from app.gui.models import ActivityEntry, ActivitySnapshot
from app.operations_core import ApplicationState


_COALESCED_PROGRESS_TITLES = {
    "Broker Rest Observed",
    "Reference Warmup Completed",
}


def _coalesce_progress(entries):
    """Keep only the newest row for high-frequency progress notifications."""
    seen: set[tuple[str, str, str, str | None]] = set()
    result = []
    for entry in entries:
        key = (
            entry.title,
            entry.source,
            entry.category.value,
            entry.related_symbol,
        )
        if entry.title in _COALESCED_PROGRESS_TITLES:
            if key in seen:
                continue
            seen.add(key)
        result.append(entry)
    return tuple(result)


def project_timeline_activity(
    state: ApplicationState,
    *,
    limit: int | None = None,
) -> ActivitySnapshot:
    """Prepare an immutable activity view model from projected timeline state."""

    if not isinstance(state, ApplicationState):
        raise TypeError("state must be an ApplicationState")
    if limit is not None and (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
    ):
        raise ValueError("limit must be a positive integer or None")

    if state.timeline_projection.entries:
        entries = _coalesce_progress(state.timeline_projection.entries)
        if limit is not None:
            entries = entries[:limit]
        return ActivitySnapshot(
            entries=tuple(
                ActivityEntry(
                    occurred_at=entry.timestamp,
                    message=f"{entry.title}: {entry.description}",
                    category=entry.category.value,
                    severity=entry.severity.value,
                    source=entry.source,
                    related_symbol=entry.related_symbol,
                    related_order_id=entry.related_order_id,
                )
                for entry in entries
            )
        )

    legacy_entries = state.timeline[::-1]
    if limit is not None:
        legacy_entries = legacy_entries[:limit]
    return ActivitySnapshot(
        entries=tuple(
            ActivityEntry(
                occurred_at=entry.occurred_at,
                message=entry.message,
                source=entry.source,
            )
            for entry in legacy_entries
        )
    )


__all__ = ["project_timeline_activity"]
