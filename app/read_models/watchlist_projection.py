from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from threading import RLock

from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeWatchlistQuote,
    RuntimeWatchlistUpdate,
)
from app.operations_core import (
    OperationsBus,
    OperationsWatchlistEntry,
    OperationsWatchlistState,
    WatchlistUpdated,
)
from app.read_models.watchlist import WatchlistEntry, WatchlistState


class WatchlistProjection:
    """Fold structured watchlist and quote events into immutable state."""

    def __init__(
        self,
        bus: OperationsBus,
        *,
        maximum_symbols: int = 100,
        stale_after: timedelta = timedelta(seconds=30),
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        if (
            isinstance(maximum_symbols, bool)
            or not isinstance(maximum_symbols, int)
            or maximum_symbols < 1
        ):
            raise ValueError("maximum_symbols must be a positive integer")
        if not isinstance(stale_after, timedelta) or stale_after <= timedelta():
            raise ValueError("stale_after must be a positive timedelta")
        self._bus = bus
        self._maximum_symbols = maximum_symbols
        self._stale_after = stale_after
        self._lock = RLock()
        self._snapshot = WatchlistState.initial()
        self._seen_events: frozenset[tuple[str, int]] = frozenset()
        self._as_of: datetime | None = None

    @property
    def snapshot(self) -> WatchlistState:
        with self._lock:
            return self._snapshot

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        identity = (event.source, event.sequence)
        with self._lock:
            if identity in self._seen_events:
                return
            self._seen_events = self._seen_events | {identity}
            self._as_of = max(
                value
                for value in (self._as_of, event.timestamp)
                if value is not None
            )
            projected = _reduce_watchlist(
                self._snapshot,
                event,
                maximum_symbols=self._maximum_symbols,
                stale_after=self._stale_after,
                as_of=self._as_of,
            )
            if projected == self._snapshot:
                return
            self._snapshot = projected

        self._bus.publish(
            WatchlistUpdated(
                occurred_at=event.timestamp,
                source="paper-runtime-watchlist-projection",
                state=_to_operations(projected),
            )
        )


def _reduce_watchlist(
    current: WatchlistState,
    event: PaperRuntimeEvent,
    *,
    maximum_symbols: int,
    stale_after: timedelta,
    as_of: datetime,
) -> WatchlistState:
    ordered = list(current.ordered_symbols)
    entries = {
        entry.symbol: _refresh_stale(
            entry,
            as_of=as_of,
            stale_after=stale_after,
        )
        for entry in current.entries
    }
    selected = current.selected_symbol
    update = _event_update(event)

    if update is not None:
        symbol = update.symbol
        if update.subscribed is True and symbol not in entries:
            if len(ordered) < maximum_symbols:
                ordered.append(symbol)
                entries[symbol] = WatchlistEntry(symbol=symbol)
        elif update.subscribed is False and symbol in entries:
            ordered.remove(symbol)
            del entries[symbol]
            if selected == symbol:
                selected = None

        if symbol in entries:
            entry = entries[symbol]
            if update.quote is not None:
                quote = update.quote
                explicit_stale = quote.stale
                stale = (
                    explicit_stale
                    if explicit_stale is not None
                    else as_of - quote.timestamp > stale_after
                )
                entry = replace(
                    entry,
                    latest_price=(
                        entry.latest_price
                        if quote.latest_price is None
                        else _decimal_text(quote.latest_price)
                    ),
                    change=(
                        entry.change
                        if quote.change is None
                        else _decimal_text(quote.change)
                    ),
                    change_percent=(
                        entry.change_percent
                        if quote.change_percent is None
                        else _decimal_text(quote.change_percent)
                    ),
                    bid=(
                        entry.bid
                        if quote.bid is None
                        else _decimal_text(quote.bid)
                    ),
                    ask=(
                        entry.ask
                        if quote.ask is None
                        else _decimal_text(quote.ask)
                    ),
                    volume=(
                        entry.volume
                        if quote.volume is None
                        else quote.volume
                    ),
                    last_update=quote.timestamp,
                    stale=stale,
                )
            if update.market_status is not None:
                entry = replace(
                    entry,
                    market_status=update.market_status,
                )
            if update.metadata is not None:
                entry = replace(entry, metadata=update.metadata)
            entries[symbol] = entry
        elif symbol is None and update.market_status is not None:
            entries = {
                item_symbol: replace(
                    entry,
                    market_status=update.market_status,
                )
                for item_symbol, entry in entries.items()
            }

        if update.selection_changed:
            if (
                update.selected_symbol is None
                or update.selected_symbol in entries
            ):
                selected = update.selected_symbol

    return WatchlistState(
        ordered_symbols=tuple(ordered),
        entries=tuple(entries[symbol] for symbol in ordered),
        selected_symbol=selected,
    )


def _event_update(
    event: PaperRuntimeEvent,
) -> RuntimeWatchlistUpdate | None:
    if event.watchlist is not None:
        return event.watchlist
    event_type = event.event_type.strip().upper()
    if event.symbol is not None and event_type in {
        "SYMBOL_SUBSCRIBED",
        "WATCHLIST_SYMBOL_ADDED",
    }:
        return RuntimeWatchlistUpdate(
            symbol=event.symbol,
            subscribed=True,
        )
    if event.symbol is not None and event_type in {
        "SYMBOL_UNSUBSCRIBED",
        "WATCHLIST_SYMBOL_REMOVED",
    }:
        return RuntimeWatchlistUpdate(
            symbol=event.symbol,
            subscribed=False,
        )
    if (
        event.symbol is not None
        and event.mark_price is not None
        and event_type in {"QUOTE_UPDATED", "MARK_UPDATED"}
    ):
        return RuntimeWatchlistUpdate(
            symbol=event.symbol,
            quote=RuntimeWatchlistQuote(
                timestamp=event.timestamp,
                latest_price=event.mark_price,
            ),
        )
    return None


def _refresh_stale(
    entry: WatchlistEntry,
    *,
    as_of: datetime,
    stale_after: timedelta,
) -> WatchlistEntry:
    if entry.last_update is None:
        return entry
    stale = (
        entry.stale is True
        or as_of - entry.last_update > stale_after
    )
    if stale == entry.stale:
        return entry
    return replace(entry, stale=stale)


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _to_operations(state: WatchlistState) -> OperationsWatchlistState:
    return OperationsWatchlistState(
        ordered_symbols=state.ordered_symbols,
        entries=tuple(
            OperationsWatchlistEntry(
                symbol=entry.symbol,
                latest_price=entry.latest_price,
                change=entry.change,
                change_percent=entry.change_percent,
                bid=entry.bid,
                ask=entry.ask,
                volume=entry.volume,
                market_status=entry.market_status,
                last_update=entry.last_update,
                stale=entry.stale,
                metadata=entry.metadata,
            )
            for entry in state.entries
        ),
        selected_symbol=state.selected_symbol,
    )


__all__ = ["WatchlistProjection"]
