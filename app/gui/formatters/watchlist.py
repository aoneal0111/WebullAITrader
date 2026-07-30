from __future__ import annotations

from decimal import Decimal

from app.gui.models.watchlist import WatchlistRow, WatchlistSnapshot
from app.read_models.watchlist import WatchlistState


def format_watchlist(state: WatchlistState) -> WatchlistSnapshot:
    if not isinstance(state, WatchlistState):
        raise TypeError("state must be a WatchlistState")
    return WatchlistSnapshot(
        rows=tuple(
            WatchlistRow(
                symbol=entry.symbol,
                selected=entry.symbol == state.selected_symbol,
                latest_price=_number(entry.latest_price),
                change=_number(entry.change, signed=True),
                change_percent=_percent(entry.change_percent),
                bid=_number(entry.bid),
                ask=_number(entry.ask),
                volume=(
                    f"{entry.volume:,}"
                    if entry.volume is not None
                    else "--"
                ),
                market_status=entry.market_status or "--",
                last_update=(
                    entry.last_update.astimezone().strftime("%H:%M:%S")
                    if entry.last_update is not None
                    else "--"
                ),
                stale=(
                    "STALE"
                    if entry.stale is True
                    else "LIVE"
                    if entry.stale is False
                    else "--"
                ),
            )
            for entry in state.entries
        )
    )


def _number(value: str | None, *, signed: bool = False) -> str:
    if value is None:
        return "--"
    number = Decimal(value)
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:,.2f}"


def _percent(value: str | None) -> str:
    if value is None:
        return "--"
    number = Decimal(value)
    prefix = "+" if number > 0 else ""
    return f"{prefix}{number:.2f}%"
