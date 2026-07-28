from __future__ import annotations

from dataclasses import dataclass

from app.operations_core import (
    DecisionsUpdated,
    OperatorDecisionSelected,
    TradeLifecycleUpdated,
)
from app.recording import RecordedSession
from app.replay import ReplayEventArchive

from .models import IndexedEvent, IndexedSession


IndexEntries = tuple[tuple[str, tuple[int, ...]], ...]


@dataclass(frozen=True, slots=True)
class EventStoreIndex:
    sessions: tuple[IndexedSession, ...] = ()
    events: tuple[IndexedEvent, ...] = ()
    timestamp_index: tuple[int, ...] = ()
    symbol_index: IndexEntries = ()
    event_type_index: IndexEntries = ()
    session_index: IndexEntries = ()
    order_index: IndexEntries = ()
    position_index: IndexEntries = ()
    decision_index: IndexEntries = ()
    lifecycle_index: IndexEntries = ()


def build_index(
    recordings: tuple[
        tuple[RecordedSession, ReplayEventArchive, str],
        ...,
    ],
) -> EventStoreIndex:
    sessions: list[IndexedSession] = []
    events: list[IndexedEvent] = []
    for session, archive, file_path in recordings:
        sessions.append(
            IndexedSession(
                session_id=session.session_id,
                file_path=file_path,
                started_at=session.started_at,
                ended_at=session.ended_at,
                strategy_version=session.strategy_version,
                application_version=session.application_version,
                broker=session.broker,
                runtime_mode=session.runtime_mode,
                event_count=len(archive.entries),
            )
        )
        for entry in archive.entries:
            event = entry.event_payload
            symbols = _symbols(event)
            order_ids = _values(event, "order_id", "orders")
            position_ids = _values(event, "position_id", ())
            decisions = _decisions(event)
            phases = (
                (event.phase,)
                if isinstance(event, TradeLifecycleUpdated)
                else ()
            )
            events.append(
                IndexedEvent(
                    session_id=session.session_id,
                    sequence_number=entry.sequence_number,
                    timestamp=entry.timestamp,
                    event_type=entry.event_type,
                    symbols=symbols,
                    order_ids=order_ids,
                    position_ids=position_ids,
                    decisions=decisions,
                    lifecycle_phases=phases,
                    summary=_summary(event),
                    event=event,
                )
            )
    sessions.sort(key=lambda item: (item.started_at, item.session_id))
    events.sort(
        key=lambda item: (
            item.timestamp,
            item.session_id,
            item.sequence_number,
        )
    )
    immutable_events = tuple(events)
    return EventStoreIndex(
        sessions=tuple(sessions),
        events=immutable_events,
        timestamp_index=tuple(range(len(immutable_events))),
        symbol_index=_make_index(
            immutable_events,
            lambda item: item.symbols,
        ),
        event_type_index=_make_index(
            immutable_events,
            lambda item: (item.event_type,),
        ),
        session_index=_make_index(
            immutable_events,
            lambda item: (item.session_id,),
        ),
        order_index=_make_index(
            immutable_events,
            lambda item: item.order_ids,
        ),
        position_index=_make_index(
            immutable_events,
            lambda item: item.position_ids,
        ),
        decision_index=_make_index(
            immutable_events,
            lambda item: item.decisions,
        ),
        lifecycle_index=_make_index(
            immutable_events,
            lambda item: item.lifecycle_phases,
        ),
    )


def _make_index(events, keys) -> IndexEntries:
    values: dict[str, list[int]] = {}
    for position, event in enumerate(events):
        for key in keys(event):
            values.setdefault(key, []).append(position)
    return tuple(
        (key, tuple(positions))
        for key, positions in sorted(values.items())
    )


def _symbols(event) -> tuple[str, ...]:
    values: list[str] = []
    symbol = getattr(event, "symbol", None)
    if isinstance(symbol, str):
        values.append(symbol)
    for collection_name in ("orders", "positions", "decisions"):
        for item in getattr(event, collection_name, ()):
            item_symbol = getattr(item, "symbol", None)
            if isinstance(item_symbol, str):
                values.append(item_symbol)
    snapshot = getattr(event, "snapshot", None)
    for item_symbol in getattr(snapshot, "symbols", ()):
        if isinstance(item_symbol, str):
            values.append(item_symbol)
    return tuple(dict.fromkeys(values))


def _values(event, attribute: str, collection) -> tuple[str, ...]:
    values: list[str] = []
    direct = getattr(event, attribute, None)
    if isinstance(direct, str):
        values.append(direct)
    names = (collection,) if isinstance(collection, str) else collection
    for name in names:
        for item in getattr(event, name, ()):
            value = getattr(item, attribute, None)
            if isinstance(value, str):
                values.append(value)
    return tuple(dict.fromkeys(values))


def _decisions(event) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(event, DecisionsUpdated):
        for decision in event.decisions:
            values.extend(
                (
                    decision.symbol,
                    decision.action,
                    decision.source_action,
                )
            )
    if isinstance(event, OperatorDecisionSelected):
        values.extend((event.symbol, event.decision_id))
    return tuple(dict.fromkeys(values))


def _summary(event) -> str:
    details = ", ".join(
        f"{name}={value}"
        for name, value in vars(event).items()
    ) if hasattr(event, "__dict__") else repr(event)
    return f"{type(event).__name__}: {details}"
