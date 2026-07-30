from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from app.operations.runtime import PaperRuntimeEvent


def projection_event_id(
    projection_name: str,
    event: PaperRuntimeEvent,
) -> UUID:
    """Derive a stable Operations event ID from an immutable runtime event."""

    if not projection_name.strip():
        raise ValueError("projection_name is required")
    if not isinstance(event, PaperRuntimeEvent):
        raise TypeError("event must be a PaperRuntimeEvent")
    return uuid5(
        NAMESPACE_URL,
        (
            "atlas-runtime-projection:"
            f"{projection_name}:{event.source}:{event.sequence}"
        ),
    )


__all__ = ["projection_event_id"]
