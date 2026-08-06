from __future__ import annotations

from decimal import Decimal

from app.gui.models.watchlist import WatchlistRow, WatchlistSnapshot
from app.read_models.watchlist import WatchlistState
from app.read_models.health import HealthState


def format_watchlist(state: WatchlistState) -> WatchlistSnapshot:
    return format_sorted_watchlist(state)


def format_sorted_watchlist(
    state: WatchlistState,
    *,
    sort_field: str = "projection",
    descending: bool = False,
    health: HealthState | None = None,
) -> WatchlistSnapshot:
    if not isinstance(state, WatchlistState):
        raise TypeError("state must be a WatchlistState")
    if sort_field not in {
        "projection",
        "symbol",
        "latest_price",
        "change_percent",
        "volume",
        "market_status",
        "stale",
    }:
        raise ValueError("unsupported watchlist sort field")
    entries = state.entries
    if sort_field == "projection" and any(
        dict(entry.metadata).get("scanner_rank") for entry in entries
    ):
        entries = tuple(
            sorted(
                entries,
                key=lambda entry: int(
                    dict(entry.metadata).get("scanner_rank", "999999")
                ),
            )
        )
    elif sort_field != "projection":
        known = tuple(
            entry
            for entry in entries
            if _sort_value(entry, sort_field) is not None
        )
        unknown = tuple(
            entry
            for entry in entries
            if _sort_value(entry, sort_field) is None
        )
        entries = (
            *sorted(
                known,
                key=lambda entry: _sort_value(entry, sort_field),
                reverse=descending,
            ),
            *unknown,
        )
    empty_title, empty_detail = _empty_state(health)
    return WatchlistSnapshot(
        rows=tuple(
            _row(entry, state.selected_symbol)
            for entry in entries
        ),
        sort_field=sort_field,
        descending=descending,
        empty_title=empty_title,
        empty_detail=empty_detail,
    )


def _empty_state(health: HealthState | None) -> tuple[str, str]:
    scanner_status = (health.scanner_status or "") if health else ""
    if scanner_status.startswith("PAUSED"):
        reason = (
            "Overnight subscription required."
            if health and health.entitlement_status == "NOT_SUBSCRIBED"
            else (health.last_warning if health else None)
            or "A required capability is unavailable."
        )
        return (
            "AI Scanner paused.",
            f"Reason:\n\n{reason}\n\n"
            "Atlas will resume automatically\n"
            "when the capability becomes available.",
        )
    if scanner_status in {"IDLE", "READY", "WAITING"}:
        return "Waiting for the next scan cycle.", ""
    return (
        "Atlas is scanning the market.",
        "High-confidence opportunities\n"
        "will appear here automatically.",
    )


def _row(entry, selected_symbol: str | None) -> WatchlistRow:
    metadata = dict(entry.metadata)
    catalyst = metadata.get("scanner_catalyst", "--")
    headline = metadata.get("scanner_catalyst_headline", "--")
    if catalyst != "--" and headline != "--":
        catalyst = f"{catalyst}: {headline}"
    return WatchlistRow(
                symbol=entry.symbol,
                selected=entry.symbol == selected_symbol,
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
                rank=metadata.get("scanner_rank", "--"),
                score=metadata.get("scanner_score", "--"),
                relative_volume=_multiple(
                    metadata.get("scanner_relative_volume")
                ),
                dollar_volume=_money(
                    metadata.get("scanner_dollar_volume")
                ),
                spread=_percent(
                    metadata.get("scanner_spread")
                ),
                catalyst=catalyst,
                passed_rules=metadata.get("scanner_passed_rules", "--"),
                failed_rules=metadata.get("scanner_failed_rules", "--"),
                freshness=metadata.get("scanner_freshness", "--"),
                session=metadata.get("scanner_session", "--"),
    )


def _sort_value(entry, sort_field: str):
    value = getattr(entry, sort_field)
    if sort_field in {"latest_price", "change_percent"}:
        return Decimal(value) if value is not None else None
    if sort_field == "stale":
        return int(value) if value is not None else None
    return value


def _number(value: str | None, *, signed: bool = False) -> str:
    if value is None:
        return "--"
    number = Decimal(value)
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:,.2f}"


def _percent(value: str | None) -> str:
    if value is None or value == "--":
        return "--"
    number = Decimal(value)
    prefix = "+" if number > 0 else ""
    return f"{prefix}{number:.2f}%"


def _multiple(value: str | None) -> str:
    return "--" if value is None else f"{Decimal(value):,.2f}x"


def _money(value: str | None) -> str:
    return "--" if value is None else f"${Decimal(value):,.0f}"
