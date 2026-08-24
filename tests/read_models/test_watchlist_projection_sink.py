from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeWatchlistQuote,
    RuntimeWatchlistUpdate,
)
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    WatchlistUpdated,
)
from app.read_models.watchlist import WatchlistState
from app.read_models.watchlist_projection import WatchlistProjection


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def event(
    sequence: int,
    *,
    update: RuntimeWatchlistUpdate | None = None,
    timestamp: datetime | None = None,
    event_type: str = "WATCHLIST_UPDATED",
    source: str = "market-runtime",
) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=timestamp or NOW + timedelta(seconds=sequence),
        event_type=event_type,
        message=event_type.replace("_", " ").title(),
        cycle=1,
        source=source,
        watchlist=update,
    )


def subscribe(sequence: int, symbol: str) -> PaperRuntimeEvent:
    return event(
        sequence,
        update=RuntimeWatchlistUpdate(
            symbol=symbol,
            subscribed=True,
        ),
    )


def quote(
    sequence: int,
    symbol: str,
    *,
    timestamp: datetime | None = None,
    latest_price: str = "101.25",
    change: str = "1.25",
    change_percent: str = "1.25",
) -> PaperRuntimeEvent:
    occurred_at = timestamp or NOW + timedelta(seconds=sequence)
    return event(
        sequence,
        timestamp=occurred_at,
        event_type="QUOTE_UPDATED",
        update=RuntimeWatchlistUpdate(
            symbol=symbol,
            quote=RuntimeWatchlistQuote(
                timestamp=occurred_at,
                latest_price=Decimal(latest_price),
                change=Decimal(change),
                change_percent=Decimal(change_percent),
                bid=Decimal("101.20"),
                ask=Decimal("101.30"),
                volume=100_000,
            ),
        ),
    )


def test_empty_watchlist_is_immutable_and_emits_no_empty_update() -> None:
    bus = OperationsBus()
    updates = []
    bus.subscribe(WatchlistUpdated, updates.append)
    projection = WatchlistProjection(bus)

    projection(event(1, event_type="HEARTBEAT"))

    assert projection.snapshot == WatchlistState.initial()
    assert updates == []
    with pytest.raises(FrozenInstanceError):
        projection.snapshot.selected_symbol = "AAPL"  # type: ignore[misc]


def test_scanner_watching_candidate_projects_into_watchlist() -> None:
    projection = WatchlistProjection(OperationsBus())

    projection(
        event(
            1,
            event_type="SCANNER_CANDIDATE_WATCHING",
            update=RuntimeWatchlistUpdate(
                symbol="LUCY",
                subscribed=True,
                quote=RuntimeWatchlistQuote(
                    timestamp=NOW + timedelta(seconds=1),
                    latest_price=Decimal("4.20"),
                    change_percent=Decimal("14"),
                    volume=950_000,
                    stale=False,
                ),
                market_status="REGULAR",
                metadata=(
                    ("scanner_rank", "2"),
                    ("scanner_score", "82"),
                    ("scanner_relative_volume", "7"),
                    ("scanner_catalyst", "NONE"),
                    (
                        "technical_qualifies_without_catalyst",
                        "true",
                    ),
                ),
            ),
        )
    )

    assert projection.snapshot.ordered_symbols == ("LUCY",)

    entry = projection.snapshot.entries[0]
    metadata = dict(entry.metadata)

    assert entry.symbol == "LUCY"
    assert entry.latest_price == "4.20"
    assert entry.change_percent == "14"
    assert entry.volume == 950_000
    assert entry.market_status == "REGULAR"
    assert entry.stale is False

    assert metadata["scanner_rank"] == "2"
    assert metadata["scanner_score"] == "82"
    assert metadata["scanner_relative_volume"] == "7"
    assert metadata["scanner_catalyst"] == "NONE"
    assert metadata["technical_qualifies_without_catalyst"] == "true"


def test_symbols_are_added_and_removed_in_subscription_order() -> None:
    projection = WatchlistProjection(OperationsBus())

    projection(subscribe(1, "aapl"))
    projection(subscribe(2, "MSFT"))
    projection(
        event(
            3,
            update=RuntimeWatchlistUpdate(
                symbol="AAPL",
                subscribed=False,
            ),
        )
    )

    assert projection.snapshot.ordered_symbols == ("MSFT",)
    assert tuple(
        entry.symbol for entry in projection.snapshot.entries
    ) == ("MSFT",)


def test_established_symbol_event_types_remain_supported() -> None:
    projection = WatchlistProjection(OperationsBus())

    projection(
        PaperRuntimeEvent(
            sequence=1,
            timestamp=NOW,
            event_type="SYMBOL_SUBSCRIBED",
            message="Subscribed.",
            cycle=1,
            symbol="AAPL",
        )
    )
    projection(
        PaperRuntimeEvent(
            sequence=2,
            timestamp=NOW + timedelta(seconds=1),
            event_type="MARK_UPDATED",
            message="Mark updated.",
            cycle=1,
            symbol="AAPL",
            mark_price=Decimal("102.50"),
        )
    )

    assert projection.snapshot.symbols == ("AAPL",)
    assert projection.snapshot.entries[0].latest_price == "102.50"


def test_maximum_watchlist_size_preserves_existing_order() -> None:
    projection = WatchlistProjection(
        OperationsBus(),
        maximum_symbols=2,
    )

    projection(subscribe(1, "AAPL"))
    projection(subscribe(2, "MSFT"))
    projection(subscribe(3, "TSLA"))

    assert projection.snapshot.ordered_symbols == ("AAPL", "MSFT")


def test_quote_update_projects_all_available_quote_fields() -> None:
    projection = WatchlistProjection(OperationsBus())
    projection(subscribe(1, "AAPL"))

    projection(quote(2, "AAPL"))

    entry = projection.snapshot.entries[0]
    assert entry.latest_price == "101.25"
    assert entry.change == "1.25"
    assert entry.change_percent == "1.25"
    assert entry.bid == "101.20"
    assert entry.ask == "101.30"
    assert entry.volume == 100_000
    assert entry.last_update == NOW + timedelta(seconds=2)
    assert entry.stale is False


def test_duplicate_quote_event_is_idempotent() -> None:
    bus = OperationsBus()
    updates = []
    bus.subscribe(WatchlistUpdated, updates.append)
    projection = WatchlistProjection(bus)
    projection(subscribe(1, "AAPL"))
    quote_event = quote(2, "AAPL")

    projection(quote_event)
    projection(quote_event)

    assert len(updates) == 2
    assert projection.snapshot.entries[0].latest_price == "101.25"


def test_quote_becomes_stale_when_later_event_advances_runtime_time() -> None:
    projection = WatchlistProjection(
        OperationsBus(),
        stale_after=timedelta(seconds=30),
    )
    projection(subscribe(1, "AAPL"))
    projection(
        quote(
            2,
            "AAPL",
            timestamp=NOW + timedelta(seconds=2),
        )
    )

    projection(
        event(
            3,
            timestamp=NOW + timedelta(seconds=33),
            event_type="HEARTBEAT",
        )
    )

    assert projection.snapshot.entries[0].stale is True


def test_multiple_symbols_keep_independent_quotes_and_global_market_status() -> None:
    projection = WatchlistProjection(OperationsBus())
    projection(subscribe(1, "AAPL"))
    projection(subscribe(2, "MSFT"))
    projection(quote(3, "AAPL", latest_price="101.25"))
    projection(quote(4, "MSFT", latest_price="420.50"))
    projection(
        event(
            5,
            update=RuntimeWatchlistUpdate(market_status="OPEN"),
            event_type="MARKET_STATUS_UPDATED",
        )
    )

    by_symbol = {
        entry.symbol: entry
        for entry in projection.snapshot.entries
    }
    assert projection.snapshot.ordered_symbols == ("AAPL", "MSFT")
    assert by_symbol["AAPL"].latest_price == "101.25"
    assert by_symbol["MSFT"].latest_price == "420.50"
    assert {entry.market_status for entry in by_symbol.values()} == {"OPEN"}


def test_selection_changes_and_removing_selection_clears_it() -> None:
    projection = WatchlistProjection(OperationsBus())
    projection(subscribe(1, "AAPL"))
    projection(subscribe(2, "MSFT"))

    projection(
        event(
            3,
            update=RuntimeWatchlistUpdate(
                selection_changed=True,
                selected_symbol="MSFT",
            ),
            event_type="WATCHLIST_SELECTION_CHANGED",
        )
    )
    assert projection.snapshot.selected_symbol == "MSFT"

    projection(
        event(
            4,
            update=RuntimeWatchlistUpdate(
                symbol="MSFT",
                subscribed=False,
            ),
        )
    )
    assert projection.snapshot.selected_symbol is None


def test_symbol_metadata_updates_without_reordering() -> None:
    projection = WatchlistProjection(OperationsBus())
    projection(subscribe(1, "AAPL"))

    projection(
        event(
            2,
            update=RuntimeWatchlistUpdate(
                symbol="AAPL",
                metadata=(
                    ("display_name", "Apple Inc."),
                    ("exchange", "NASDAQ"),
                ),
            ),
            event_type="SYMBOL_METADATA_UPDATED",
        )
    )

    assert projection.snapshot.ordered_symbols == ("AAPL",)
    assert projection.snapshot.entries[0].metadata == (
        ("display_name", "Apple Inc."),
        ("exchange", "NASDAQ"),
    )


def test_deterministic_replay_produces_identical_watchlist_state() -> None:
    events = (
        subscribe(1, "AAPL"),
        subscribe(2, "MSFT"),
        quote(3, "AAPL"),
        event(
            4,
            update=RuntimeWatchlistUpdate(
                selection_changed=True,
                selected_symbol="AAPL",
            ),
        ),
        event(
            5,
            update=RuntimeWatchlistUpdate(market_status="OPEN"),
        ),
    )
    results = []

    for _ in range(2):
        projection = WatchlistProjection(OperationsBus())
        for item in events:
            projection(item)
        results.append(projection.snapshot)

    assert results[0] == results[1]


def test_application_state_exposes_watchlist_projection() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = WatchlistProjection(bus)

    projection(subscribe(1, "AAPL"))
    projection(quote(2, "AAPL"))

    assert store.snapshot().watchlist_projection == projection.snapshot
