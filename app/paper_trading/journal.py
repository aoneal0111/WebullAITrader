from __future__ import annotations

from datetime import datetime

from app.paper_trading.models import JournalEvent, JournalEventType, PaperJournal


def append_event(
    journal: PaperJournal,
    event_type: JournalEventType,
    request_id: str,
    timestamp: datetime,
    message: str,
    details: tuple[tuple[str, str], ...] = (),
) -> PaperJournal:
    if timestamp.tzinfo is None:
        raise ValueError("journal timestamps must be timezone-aware")
    event = JournalEvent(len(journal.events) + 1, event_type, request_id, timestamp, message, details)
    return PaperJournal((*journal.events, event))
