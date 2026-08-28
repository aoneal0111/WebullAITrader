from __future__ import annotations

from decimal import Decimal

from app.gui.formatters.prices import format_price

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
        scanner_status=(
            health.scanner_status
            if health is not None and health.scanner_status
            else "Unknown"
        ),
        candidate_count=len(entries),
    )


def _empty_state(health: HealthState | None) -> tuple[str, str]:
    scanner_status = (health.scanner_status or "") if health else ""
    if scanner_status == "CAPABILITY_PAUSED" or scanner_status.startswith("PAUSED"):
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
        "Atlas is scanning",
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
                freshness=_freshness(
                    metadata.get("scanner_freshness", "--"),
                    metadata.get("scanner_market_age_ms"),
                    last_state=metadata.get("scanner_last_price_freshness"),
                    last_age_ms=metadata.get("scanner_last_price_age_ms"),
                    quote_state=metadata.get("scanner_quote_freshness"),
                    quote_age_ms=metadata.get("scanner_quote_age_ms"),
                ),
                session=metadata.get("scanner_session", "--"),
                classification=metadata.get(
                    "scanner_classification",
                    "--",
                ),
                market_timestamp=metadata.get("scanner_market_timestamp", "--"),
                float_shares=_shares(metadata.get("warrior_float")),
                setup=metadata.get("warrior_setup", "--").replace("_", " "),
                setup_state=metadata.get("warrior_setup_state", "--").replace("_", " "),
                distance_to_hod=_percent(metadata.get("warrior_distance_hod")),
                strategy_status=metadata.get("warrior_status", "--").replace("_", " "),
                explanations=metadata.get("warrior_explanations", "--"),
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
    return f"{prefix}{format_price(number)}"


def _percent(value: str | None) -> str:
    if value is None or value == "--":
        return "--"
    number = Decimal(value)
    prefix = "+" if number > 0 else ""
    return f"{prefix}{number:.2f}%"


def _multiple(value: str | None) -> str:
    return "--" if value is None else f"{Decimal(value):,.2f}x"


def _freshness(
    state: str,
    age_ms: str | None,
    *,
    last_state: str | None = None,
    last_age_ms: str | None = None,
    quote_state: str | None = None,
    quote_age_ms: str | None = None,
) -> str:
    if state == "--" or age_ms is None:
        return state
    if (
        last_state is not None
        and quote_state is not None
        and (last_state != "MISSING" or quote_state != "MISSING")
    ):
        return " | ".join((
            _component_freshness("LAST", last_state, last_age_ms),
            _component_freshness("QUOTE", quote_state, quote_age_ms),
            f"ENTRY DATA {state}",
        ))
    return f"{state} | {Decimal(age_ms) / Decimal('1000'):.1f}s"


def _component_freshness(
    label: str, state: str, age_ms: str | None,
) -> str:
    if state == "LIVE":
        return f"{label} LIVE"
    if age_ms not in {None, "--"}:
        return f"{label} {Decimal(age_ms) / Decimal('1000'):.1f}s"
    return f"{label} {state}"


def _money(value: str | None) -> str:
    return "--" if value is None else f"${Decimal(value):,.0f}"


def _shares(value: str | None) -> str:
    if value is None or value == "--":
        return "--"
    shares = Decimal(value)
    return f"{shares / Decimal('1000000'):.1f}M" if shares >= Decimal("1000000") else f"{shares:,.0f}"
