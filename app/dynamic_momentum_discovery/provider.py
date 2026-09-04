"""Isolated paged Webull screener research provider.

This provider is deliberately absent from normal Atlas composition.  It performs no
subscriptions and returns research rows only; it cannot mutate the production universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from math import ceil
from time import perf_counter
from typing import Callable, Mapping

from .models import DiscoverySource, SourceMembership


@dataclass(frozen=True, slots=True)
class BroadDiscoveryRow:
    symbol: str
    observed_at: datetime
    membership: SourceMembership
    price: Decimal | None
    previous_close: Decimal | None
    open_price: Decimal | None
    high: Decimal | None
    volume: Decimal | None
    relative_volume: Decimal | None
    turnover: Decimal | None
    raw_fields_json: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None
    quote_timestamp: datetime | None = None
    recent_1m_change_percent: Decimal | None = None
    recent_5m_change_percent: Decimal | None = None
    volume_acceleration: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    source: DiscoverySource
    page_index: int
    error_type: str


@dataclass(frozen=True, slots=True)
class BroadDiscoveryRefresh:
    observed_at: datetime
    session: str
    breadth_per_source: int
    page_size: int
    rows: tuple[BroadDiscoveryRow, ...]
    request_count: int
    returned_row_count: int
    unique_symbol_count: int
    request_latency_ms: float
    failures: tuple[ProviderFailure, ...]
    research_only: bool = True
    selection_authorized: bool = False
    execution_authorized: bool = False


class WebullBroadDiscoveryProvider:
    """Paged REST research reader with no production scanner callback."""

    def __init__(
        self,
        screener: object,
        *,
        page_size: int = 50,
        maximum_breadth: int = 500,
        sources: tuple[DiscoverySource, ...] = (
            DiscoverySource.SESSION_GAINERS,
            DiscoverySource.RELATIVE_VOLUME_10D,
        ),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        if page_size < 1 or page_size > 100:
            raise ValueError("research page_size must be within 1..100")
        if maximum_breadth < 1 or maximum_breadth > 500:
            raise ValueError("research maximum breadth must be within 1..500")
        if not sources:
            raise ValueError("at least one research source is required")
        self._screener = screener
        self._page_size = page_size
        self._maximum_breadth = maximum_breadth
        self._sources = tuple(dict.fromkeys(sources))
        self._timer = timer

    def fetch(
        self, *, breadth_per_source: int, observed_at: datetime, session: str,
    ) -> BroadDiscoveryRefresh:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("provider observation time must be timezone-aware")
        if breadth_per_source < 1 or breadth_per_source > self._maximum_breadth:
            raise ValueError("breadth exceeds configured research boundary")
        normalized_session = session.strip().upper()
        rows: list[BroadDiscoveryRow] = []
        failures: list[ProviderFailure] = []
        request_count = 0
        returned = 0
        started = self._timer()
        for source in self._sources:
            source_rank = 0
            for page_index in range(1, ceil(breadth_per_source / self._page_size) + 1):
                request_count += 1
                remaining = min(
                    self._page_size, breadth_per_source - source_rank
                )
                try:
                    response = _request(
                        self._screener, source, normalized_session,
                        page_index=page_index, page_size=self._page_size,
                    )
                    page_rows, has_more = _response_rows(response)
                except Exception as exc:
                    failures.append(ProviderFailure(
                        source=source, page_index=page_index,
                        error_type=type(exc).__name__,
                    ))
                    break
                returned += len(page_rows)
                for raw in page_rows[:remaining]:
                    source_rank += 1
                    symbol = str(raw.get("symbol", "")).strip().upper()
                    if not symbol:
                        continue
                    rows.append(BroadDiscoveryRow(
                        symbol=symbol,
                        observed_at=observed_at,
                        membership=SourceMembership(
                            source=source, rank=source_rank, page_index=page_index,
                        ),
                        price=_decimal(raw, "price", "close"),
                        previous_close=_decimal(raw, "pre_close", "previous_close"),
                        open_price=_decimal(raw, "open"),
                        high=_decimal(raw, "high"),
                        volume=_decimal(raw, "volume"),
                        relative_volume=_decimal(raw, "relative_volume_10d"),
                        turnover=_decimal(raw, "turnover"),
                        raw_fields_json=json.dumps(
                            dict(raw), default=str, sort_keys=True,
                            separators=(",", ":"),
                        ),
                        bid=_decimal(raw, "bid", "bid_price"),
                        ask=_decimal(raw, "ask", "ask_price"),
                        bid_size=_decimal(raw, "bid_size", "bid_volume"),
                        ask_size=_decimal(raw, "ask_size", "ask_volume"),
                        quote_timestamp=_datetime(
                            raw, "quote_timestamp", "timestamp", "time"
                        ),
                        recent_1m_change_percent=_decimal(
                            raw, "change_1m_percent", "change_ratio_1m"
                        ),
                        recent_5m_change_percent=_decimal(
                            raw, "change_5m_percent", "change_ratio_5m"
                        ),
                        volume_acceleration=_decimal(
                            raw, "volume_acceleration"
                        ),
                    ))
                    if source_rank >= breadth_per_source:
                        break
                if source_rank >= breadth_per_source:
                    break
                if has_more is False or len(page_rows) < self._page_size:
                    break
        latency = max(0.0, (self._timer() - started) * 1000.0)
        return BroadDiscoveryRefresh(
            observed_at=observed_at,
            session=normalized_session,
            breadth_per_source=breadth_per_source,
            page_size=self._page_size,
            rows=tuple(rows),
            request_count=request_count,
            returned_row_count=returned,
            unique_symbol_count=len({row.symbol for row in rows}),
            request_latency_ms=latency,
            failures=tuple(failures),
        )


def source_rows_by_symbol(
    refresh: BroadDiscoveryRefresh,
) -> dict[str, tuple[BroadDiscoveryRow, ...]]:
    grouped: dict[str, list[BroadDiscoveryRow]] = {}
    for row in refresh.rows:
        grouped.setdefault(row.symbol, []).append(row)
    return {
        symbol: tuple(sorted(values, key=lambda item: (
            item.membership.source.value, item.membership.rank
        )))
        for symbol, values in grouped.items()
    }


def _request(screener, source, session, *, page_index, page_size):
    if source is DiscoverySource.SESSION_GAINERS:
        rank_type = {
            "PRE_MARKET": "PRE_MARKET", "PREMARKET": "PRE_MARKET",
            "AFTER_HOURS": "AFTER_MARKET", "AFTER_MARKET": "AFTER_MARKET",
        }.get(session, "DAY_1")
        return screener.get_gainers_losers(
            rank_type, "US_STOCK", "CHANGE_RATIO",
            page_index=page_index, page_size=page_size, direction="DESC",
        )
    sort = {
        DiscoverySource.RELATIVE_VOLUME_10D: "RELATIVE_VOLUME_10D",
        DiscoverySource.VOLUME_LEADERS: "VOLUME",
        DiscoverySource.TURNOVER_LEADERS: "TURNOVER",
    }[source]
    return screener.get_most_active(
        "US_STOCK", sort_by=sort,
        page_index=page_index, page_size=page_size, direction="DESC",
    )


def _response_rows(response) -> tuple[tuple[Mapping[str, object], ...], bool | None]:
    payload = response.json() if callable(getattr(response, "json", None)) else response
    if isinstance(payload, Mapping):
        has_more = _optional_bool(payload.get("has_more"))
        container = payload.get("data", payload.get("rows", ()))
        if isinstance(container, Mapping):
            has_more = _optional_bool(container.get("has_more", has_more))
            container = container.get("data", container.get("rows", ()))
    else:
        has_more = None
        container = payload
    if not isinstance(container, (list, tuple)):
        return (), has_more
    return tuple(row for row in container if isinstance(row, Mapping)), (
        has_more
    )


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
        return None
    return bool(value)


def _decimal(row: Mapping[str, object], *names: str) -> Decimal | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError):
                return None
    return None


def _datetime(row: Mapping[str, object], *names: str) -> datetime | None:
    for name in names:
        value = row.get(name)
        if value in (None, ""):
            continue
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else None
        try:
            if isinstance(value, (int, float)) or str(value).isdigit():
                number = float(value)
                if number > 10_000_000_000:
                    number /= 1000.0
                return datetime.fromtimestamp(number, tz=UTC)
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else None
        except (ValueError, TypeError, OverflowError):
            return None
    return None


__all__ = [
    "BroadDiscoveryRefresh", "BroadDiscoveryRow", "ProviderFailure",
    "WebullBroadDiscoveryProvider", "source_rows_by_symbol",
]
