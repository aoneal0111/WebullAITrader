from __future__ import annotations

from collections import Counter
from datetime import datetime

from .index import EventStoreIndex
from .models import QueryResult, QueryStatistics


class EventStoreQueryEngine:
    def query_all(self, index: EventStoreIndex) -> QueryResult:
        return self._result(index, index.events, "all")

    def by_symbol(self, index: EventStoreIndex, symbol: str) -> QueryResult:
        return self._indexed(index, index.symbol_index, _key(symbol), f"symbol:{symbol}")

    def by_event_type(self, index, event_type: str) -> QueryResult:
        return self._indexed(index, index.event_type_index, _text(event_type), f"event:{event_type}")

    def by_session(self, index, session_id: str) -> QueryResult:
        return self._indexed(index, index.session_index, _text(session_id), f"session:{session_id}")

    def by_order_id(self, index, order_id: str) -> QueryResult:
        return self._indexed(index, index.order_index, _text(order_id), f"order:{order_id}")

    def by_position_id(self, index, position_id: str) -> QueryResult:
        return self._indexed(index, index.position_index, _text(position_id), f"position:{position_id}")

    def by_decision(self, index, decision: str) -> QueryResult:
        return self._indexed(index, index.decision_index, _text(decision), f"decision:{decision}")

    def by_lifecycle_phase(self, index, phase: str) -> QueryResult:
        return self._indexed(index, index.lifecycle_index, _key(phase), f"lifecycle:{phase}")

    def by_timestamp_range(
        self,
        index: EventStoreIndex,
        start: datetime,
        end: datetime,
    ) -> QueryResult:
        _aware(start, "start")
        _aware(end, "end")
        if end < start:
            raise ValueError("end cannot precede start")
        return self._result(
            index,
            tuple(
                event
                for event in index.events
                if start <= event.timestamp <= end
            ),
            f"time:{start.isoformat()}..{end.isoformat()}",
        )

    def search(self, index: EventStoreIndex, text: str) -> QueryResult:
        needle = _text(text).casefold()
        return self._result(
            index,
            tuple(
                event
                for event in index.events
                if needle
                in " ".join(
                    (
                        event.session_id,
                        event.event_type,
                        event.summary,
                        *event.symbols,
                        *event.order_ids,
                        *event.position_ids,
                        *event.decisions,
                        *event.lifecycle_phases,
                    )
                ).casefold()
            ),
            f"search:{text}",
        )

    def statistics(self, index: EventStoreIndex) -> QueryStatistics:
        return self._statistics(index, index.events)

    def _indexed(self, index, entries, key, description) -> QueryResult:
        positions = dict(entries).get(key, ())
        return self._result(
            index,
            tuple(index.events[position] for position in positions),
            description,
        )

    def _result(self, index, events, description) -> QueryResult:
        return QueryResult(
            description,
            tuple(events),
            self._statistics(index, tuple(events)),
        )

    def _statistics(self, index, events) -> QueryStatistics:
        timestamps = tuple(event.timestamp for event in events)
        counts = Counter(event.event_type for event in events)
        return QueryStatistics(
            total_sessions=len(index.sessions),
            total_events=len(index.events),
            matched_events=len(events),
            earliest_timestamp=min(timestamps) if timestamps else None,
            latest_timestamp=max(timestamps) if timestamps else None,
            event_type_counts=tuple(sorted(counts.items())),
        )


def _text(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("query value must be stripped non-empty text")
    return value


def _key(value: str) -> str:
    return _text(value).upper()


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
