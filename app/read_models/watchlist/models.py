from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    symbol: str
    latest_price: str | None = None
    change: str | None = None
    change_percent: str | None = None
    bid: str | None = None
    ask: str | None = None
    volume: int | None = None
    market_status: str | None = None
    last_update: datetime | None = None
    stale: bool | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.symbol, str)
            or not self.symbol.strip()
            or self.symbol != self.symbol.strip().upper()
        ):
            raise ValueError("watchlist symbol must be normalized")
        for field_name in (
            "latest_price",
            "change",
            "change_percent",
            "bid",
            "ask",
            "market_status",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"watchlist {field_name} must be None or stripped text"
                )
        if self.volume is not None and (
            isinstance(self.volume, bool)
            or not isinstance(self.volume, int)
            or self.volume < 0
        ):
            raise ValueError("watchlist volume must be nonnegative")
        if self.last_update is not None and self.last_update.tzinfo is None:
            raise ValueError("watchlist last update must be timezone-aware")
        if self.stale is not None and not isinstance(self.stale, bool):
            raise TypeError("watchlist stale must be a bool or None")
        if not isinstance(self.metadata, tuple):
            raise TypeError("watchlist metadata must be an immutable tuple")


@dataclass(frozen=True, slots=True)
class WatchlistState:
    ordered_symbols: tuple[str, ...] = ()
    entries: tuple[WatchlistEntry, ...] = ()
    selected_symbol: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ordered_symbols, tuple):
            raise TypeError("ordered_symbols must be an immutable tuple")
        if not isinstance(self.entries, tuple):
            raise TypeError("entries must be an immutable tuple")
        if any(
            not isinstance(entry, WatchlistEntry)
            for entry in self.entries
        ):
            raise TypeError("entries must contain WatchlistEntry instances")
        if tuple(entry.symbol for entry in self.entries) != (
            self.ordered_symbols
        ):
            raise ValueError("watchlist entries must follow ordered_symbols")
        if len(set(self.ordered_symbols)) != len(self.ordered_symbols):
            raise ValueError("watchlist symbols must be unique")
        if (
            self.selected_symbol is not None
            and self.selected_symbol not in self.ordered_symbols
        ):
            raise ValueError("selected_symbol must belong to the watchlist")

    @property
    def symbols(self) -> tuple[str, ...]:
        """Compatibility alias for consumers expecting a symbols tuple."""

        return self.ordered_symbols

    @classmethod
    def initial(cls) -> "WatchlistState":
        return cls()
