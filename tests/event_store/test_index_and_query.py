from datetime import timedelta

from app.event_store import EventStoreQueryEngine, build_index
from app.replay import ReplayEventArchive


def test_builds_all_immutable_indexes(event_store_sessions) -> None:
    first, first_events = event_store_sessions("one")
    second, second_events = event_store_sessions("two", 10)
    index = build_index(
        (
            (
                first,
                ReplayEventArchive.from_events(first_events),
                "one.json",
            ),
            (
                second,
                ReplayEventArchive.from_events(second_events),
                "two.json",
            ),
        )
    )

    assert len(index.timestamp_index) == 6
    assert dict(index.symbol_index)["AAPL"] == tuple(range(6))
    assert len(dict(index.event_type_index)["OrdersUpdated"]) == 2
    assert len(dict(index.session_index)["one"]) == 3
    assert len(dict(index.order_index)["order-one"]) == 2
    assert len(dict(index.position_index)["position-two"]) == 1
    assert len(dict(index.decision_index)["ENTER_LONG"]) == 2
    assert len(dict(index.lifecycle_index)["FILLED"]) == 2


def test_query_engine_supports_every_query_dimension(
    event_store_sessions,
) -> None:
    session, events = event_store_sessions("one")
    index = build_index(
        (
            (
                session,
                ReplayEventArchive.from_events(events),
                "one.json",
            ),
        )
    )
    query = EventStoreQueryEngine()

    assert query.by_symbol(index, "aapl").statistics.matched_events == 3
    assert query.by_event_type(
        index,
        "OrdersUpdated",
    ).statistics.matched_events == 1
    assert query.by_session(
        index,
        "one",
    ).statistics.matched_events == 3
    assert query.by_order_id(
        index,
        "order-one",
    ).statistics.matched_events == 2
    assert query.by_position_id(
        index,
        "position-one",
    ).statistics.matched_events == 1
    assert query.by_decision(
        index,
        "ENTER_LONG",
    ).statistics.matched_events == 1
    assert query.by_lifecycle_phase(
        index,
        "filled",
    ).statistics.matched_events == 1
    assert query.search(
        index,
        "approved",
    ).statistics.matched_events >= 1
    assert query.by_timestamp_range(
        index,
        session.started_at,
        session.started_at + timedelta(seconds=1),
    ).statistics.matched_events == 2
    statistics = query.statistics(index)
    assert statistics.total_sessions == 1
    assert statistics.total_events == 3
    assert dict(statistics.event_type_counts)["DecisionsUpdated"] == 1
