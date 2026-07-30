from __future__ import annotations

from app.gui.models import ActivityEntry, ActivitySnapshot, TimelineFilter


def format_timeline(
    snapshot: ActivitySnapshot,
    filters: TimelineFilter,
) -> ActivitySnapshot:
    """Apply operator filters to an immutable timeline presentation model."""

    if not isinstance(snapshot, ActivitySnapshot):
        raise TypeError("snapshot must be an ActivitySnapshot")
    if not isinstance(filters, TimelineFilter):
        raise TypeError("filters must be a TimelineFilter")
    return ActivitySnapshot(
        entries=tuple(
            entry
            for entry in snapshot.entries
            if _matches(entry, filters)
        ),
        filters=filters,
        severity_options=_options(
            entry.severity for entry in snapshot.entries
        ),
        category_options=_options(
            entry.category for entry in snapshot.entries
        ),
        symbol_options=_options(
            entry.related_symbol
            for entry in snapshot.entries
            if entry.related_symbol is not None
        ),
    )


def _matches(entry: ActivityEntry, filters: TimelineFilter) -> bool:
    if filters.severity != "ALL" and entry.severity != filters.severity:
        return False
    if filters.category != "ALL" and entry.category != filters.category:
        return False
    if filters.symbol != "ALL" and entry.related_symbol != filters.symbol:
        return False
    query = filters.search.strip().casefold()
    if not query:
        return True
    searchable = " ".join(
        value
        for value in (
            entry.message,
            entry.source,
            entry.related_symbol,
            entry.related_order_id,
        )
        if value is not None
    ).casefold()
    return query in searchable


def _options(values) -> tuple[str, ...]:
    return ("ALL", *sorted(set(values)))


__all__ = ["format_timeline"]
