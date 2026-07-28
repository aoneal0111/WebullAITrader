"""Read-only historical index over immutable recorded sessions."""

from .controller import EventStoreController
from .index import EventStoreIndex, build_index
from .models import (
    EventStoreSnapshot,
    EventStoreStatus,
    IndexedEvent,
    IndexedSession,
    QueryResult,
    QueryStatistics,
)
from .query import EventStoreQueryEngine
from .repository import (
    DuplicateSessionError,
    EventStoreRepository,
    EventStoreRepositoryError,
)

__all__ = [
    "DuplicateSessionError",
    "EventStoreController",
    "EventStoreIndex",
    "EventStoreQueryEngine",
    "EventStoreRepository",
    "EventStoreRepositoryError",
    "EventStoreSnapshot",
    "EventStoreStatus",
    "IndexedEvent",
    "IndexedSession",
    "QueryResult",
    "QueryStatistics",
    "build_index",
]
