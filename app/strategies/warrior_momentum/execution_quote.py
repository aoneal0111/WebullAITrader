"""Fail-closed execution-time quote confirmation contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.webull.sdk_market_data import LazyOfficialDataClient, _response_rows


@dataclass(frozen=True, slots=True)
class ExecutionQuoteSnapshot:
    symbol: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    last_timestamp: datetime
    bid_timestamp: datetime
    ask_timestamp: datetime
    confirmed_at: datetime | None = None


class WebullExecutionQuoteSource:
    """One existing Webull REST snapshot call for one actionable symbol."""

    def __init__(self, client: LazyOfficialDataClient, *, category: str = "US_STOCK",
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        if not isinstance(client, LazyOfficialDataClient):
            raise TypeError("client must be a LazyOfficialDataClient")
        self._client = client
        self._category = category
        self._clock = clock

    def __call__(self, symbol: str) -> ExecutionQuoteSnapshot | None:
        normalized = symbol.strip().upper()
        if not normalized:
            return None
        try:
            market_data = getattr(self._client.get(), "market_data")
            response = market_data.get_snapshot(
                symbols=[normalized], category=self._category,
                extend_hour_required=True,
            )
            rows = _response_rows(response)
            row = rows[0] if rows else None
            return None if row is None else parse_execution_quote(
                normalized, row, confirmed_at=self._clock()
            )
        except Exception:
            return None


def parse_execution_quote(symbol: str, row: Mapping[str, object], *,
                          confirmed_at: datetime | None = None) -> ExecutionQuoteSnapshot | None:
    """Accept only prices paired with provider-authored timestamps."""
    try:
        last = _positive(row, "price", "last", "latest_price")
        bid = _positive(row, "bid", "bid_price", "bidPrice", "bid1")
        ask = _positive(row, "ask", "ask_price", "askPrice", "ask1")
        last_time = _provider_time(row, "last_trade_time", "trade_time")
        quote_time = _provider_time(row, "quote_time", "quote_timestamp")
    except (InvalidOperation, TypeError, ValueError, OverflowError, OSError):
        return None
    if ask < bid:
        return None
    return ExecutionQuoteSnapshot(
        symbol.strip().upper(), last, bid, ask, last_time, quote_time,
        quote_time, confirmed_at,
    )


def _value(row: Mapping[str, object], *names: str) -> object | None:
    lowered = {str(key).lower(): value for key, value in row.items()}
    return next((lowered[name.lower()] for name in names if lowered.get(name.lower()) is not None), None)


def _positive(row: Mapping[str, object], *names: str) -> Decimal:
    value = _value(row, *names)
    if value is None or isinstance(value, bool):
        raise ValueError("missing execution price")
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("invalid execution price")
    return result


def _provider_time(row: Mapping[str, object], *names: str) -> datetime:
    value = _value(row, *names)
    if value is None or isinstance(value, bool):
        raise ValueError("missing provider timestamp")
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float, Decimal)) or str(value).strip().replace(".", "", 1).isdigit():
        numeric = Decimal(str(value).strip())
        divisor = Decimal("1000") if abs(numeric) >= Decimal("100000000000") else Decimal("1")
        result = datetime.fromtimestamp(float(numeric / divisor), tz=UTC)
    else:
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("provider timestamp must be timezone-aware")
    return result.astimezone(UTC)


ExecutionQuoteSource = Callable[[str], ExecutionQuoteSnapshot | None]

__all__ = ["ExecutionQuoteSnapshot", "ExecutionQuoteSource", "WebullExecutionQuoteSource", "parse_execution_quote"]
