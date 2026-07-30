from __future__ import annotations

from app.operations_core import OperationsWatchlistState
from app.read_models.watchlist.models import WatchlistEntry, WatchlistState


def project_operational_watchlist(
    state: OperationsWatchlistState,
) -> WatchlistState:
    if not isinstance(state, OperationsWatchlistState):
        raise TypeError("state must be an OperationsWatchlistState")
    return WatchlistState(
        ordered_symbols=state.ordered_symbols,
        entries=tuple(
            WatchlistEntry(
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
