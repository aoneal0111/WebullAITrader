from __future__ import annotations

from app.operations_core import OperationsTimelineEntry
from app.read_models.timeline.models import (
    TimelineCategory,
    TimelineEntry,
    TimelineReadModelSnapshot,
    TimelineSeverity,
)


def project_operational_timeline(
    entries: tuple[OperationsTimelineEntry, ...],
) -> TimelineReadModelSnapshot:
    if not isinstance(entries, tuple):
        raise TypeError("entries must be an immutable tuple")
    if any(
        not isinstance(entry, OperationsTimelineEntry)
        for entry in entries
    ):
        raise TypeError(
            "entries must contain only OperationsTimelineEntry instances"
        )

    return TimelineReadModelSnapshot(
        entries=tuple(
            TimelineEntry(
                timestamp=entry.timestamp,
                category=TimelineCategory(entry.category),
                severity=TimelineSeverity(entry.severity),
                source=entry.source,
                title=entry.title,
                description=entry.description,
                related_symbol=entry.related_symbol,
                related_order_id=entry.related_order_id,
            )
            for entry in entries
        )
    )


__all__ = ["project_operational_timeline"]
