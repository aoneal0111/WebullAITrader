"""REST-backed market-data service used by the Mission Control chart."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Callable

from app.webull.sdk_market_data import LazyOfficialDataClient


_LOGGER = logging.getLogger("atlas.market_data.chart")


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ChartMarketData:
    symbol: str
    timeframe: str
    bars: tuple[HistoricalBar, ...] = ()
    snapshot: Mapping[str, object] | None = None
    quote: Mapping[str, object] | None = None
    error: str | None = None


class ChartMarketDataService:
    """Load chart facts through REST without depending on the stream runtime."""

    def __init__(
        self,
        client: LazyOfficialDataClient,
        *,
        category: str = "US_STOCK",
        bar_count: int = 120,
        observation_sink: Callable[[str, str, int], None] | None = None,
    ) -> None:
        if not isinstance(client, LazyOfficialDataClient):
            raise TypeError("client must be a LazyOfficialDataClient")
        if bar_count < 1:
            raise ValueError("bar_count must be positive")
        self._client = client
        self._category = category
        self._bar_count = bar_count
        if observation_sink is not None and not callable(observation_sink):
            raise TypeError("observation_sink must be callable or None")
        self._observation_sink = observation_sink

    def load_historical_bars(
        self,
        symbol: str,
        timeframe: str = "1M",
    ) -> tuple[HistoricalBar, ...]:
        """Load only historical candles without snapshot/quote requests."""

        normalized = symbol.strip().upper() if isinstance(symbol, str) else ""
        if not normalized or normalized == "--":
            _skip(
                "historical_bar_request",
                normalized or "--",
                "no selected symbol",
            )
            return ()

        timespan = _timespan(timeframe)
        if timespan is None:
            _skip(
                "historical_bar_request",
                normalized,
                f"unsupported timeframe {timeframe}",
            )
            return ()

        try:
            market_data = getattr(self._client.get(), "market_data")
        except Exception as exc:
            _skip(
                "historical_bar_request",
                normalized,
                f"REST client unavailable ({type(exc).__name__})",
            )
            return ()

        response = _call(
            "historical_bar_request",
            normalized,
            lambda: market_data.get_history_bar(
                normalized,
                self._category,
                timespan,
                count=str(self._bar_count),
                real_time_required=False,
            ),
            detail=f"timespan={timespan} count={self._bar_count}",
        )

        bars = tuple(
            sorted(
                filter(None, (_bar(row) for row in _rows(response))),
                key=lambda item: item.timestamp,
            )
        )

        if bars and self._observation_sink is not None:
            self._observation_sink(
                "HISTORICAL_BARS_LOADED",
                normalized,
                len(bars),
            )

        return bars


    def load(self, symbol: str, timeframe: str = "1D") -> ChartMarketData:
        normalized = symbol.strip().upper() if isinstance(symbol, str) else ""
        if not normalized or normalized == "--" or normalized == "NO ACTIVE SYMBOL":
            _skip("snapshot_request", normalized or "--", "no selected symbol")
            _skip("quote_request", normalized or "--", "no selected symbol")
            _skip("historical_bar_request", normalized or "--", "no selected symbol")
            return ChartMarketData(
                symbol="--",
                timeframe=timeframe,
                error="No selected symbol is available for the chart.",
            )

        timespan = _timespan(timeframe)
        if timespan is None:
            reason = f"unsupported timeframe {timeframe}"
            _skip("historical_bar_request", normalized, reason)
            return ChartMarketData(normalized, timeframe, error=reason)

        try:
            market_data = getattr(self._client.get(), "market_data")
        except Exception as exc:
            reason = f"REST client unavailable ({type(exc).__name__})"
            _skip("snapshot_request", normalized, reason)
            _skip("quote_request", normalized, reason)
            _skip("historical_bar_request", normalized, reason)
            return ChartMarketData(normalized, timeframe, error=reason)

        snapshot_response = _call(
            "snapshot_request",
            normalized,
            lambda: market_data.get_snapshot(
                symbols=[normalized], category=self._category
            ),
        )
        quote_response = _call(
            "quote_request",
            normalized,
            lambda: market_data.get_quotes(normalized, self._category),
        )
        bars_response = _call(
            "historical_bar_request",
            normalized,
            lambda: market_data.get_history_bar(
                normalized,
                self._category,
                timespan,
                count=str(self._bar_count),
                real_time_required=False,
            ),
            detail=f"timespan={timespan} count={self._bar_count}",
        )
        snapshot = _first_row(snapshot_response)
        quote = _first_row(quote_response)
        bars = tuple(
            sorted(
                filter(None, (_bar(row) for row in _rows(bars_response))),
                key=lambda item: item.timestamp,
            )
        )
        if bars and self._observation_sink is not None:
            self._observation_sink("HISTORICAL_BARS_LOADED", normalized, len(bars))
        errors = []
        if bars_response is None:
            errors.append("historical bar request failed")
        elif not bars:
            errors.append("historical bar response contained no usable candles")
        return ChartMarketData(
            symbol=normalized,
            timeframe=timeframe,
            bars=bars,
            snapshot=snapshot,
            quote=quote,
            error="; ".join(errors) or None,
        )


def _call(operation, symbol, request, *, detail: str = ""):
    _LOGGER.info(
        "operation=%s status=started symbol=%s%s",
        operation,
        symbol,
        f" {detail}" if detail else "",
    )
    try:
        response = request()
    except Exception as exc:
        # Third-party exception text can contain signed headers and credentials.
        _LOGGER.warning(
            "operation=%s status=failed symbol=%s error_type=%s",
            operation,
            symbol,
            type(exc).__name__,
        )
        return None
    _LOGGER.info("operation=%s status=succeeded symbol=%s", operation, symbol)
    return response


def _skip(operation: str, symbol: str, reason: str) -> None:
    _LOGGER.info(
        "operation=%s status=skipped symbol=%s reason=%s",
        operation,
        symbol,
        reason,
    )


def _timespan(timeframe: str) -> str | None:
    return {
        "1M": "M1",
        "5M": "M5",
        "15M": "M15",
        "1H": "H1",
        "1D": "D",
    }.get(timeframe.strip().upper())


def _payload(response: object) -> object:
    if response is None:
        return None
    if isinstance(response, (Mapping, list, tuple)):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except Exception:
            return None
    return getattr(response, "data", None)


def _rows(response: object) -> tuple[Mapping[str, object], ...]:
    value = _payload(response)
    for _ in range(4):
        if isinstance(value, Mapping):
            nested = next(
                (
                    value[key]
                    for key in ("data", "items", "bars", "results", "list")
                    if key in value
                ),
                None,
            )
            if nested is None:
                return (value,)
            value = nested
            continue
        if isinstance(value, (list, tuple)):
            mappings = tuple(item for item in value if isinstance(item, Mapping))
            if len(mappings) == 1 and any(
                key in mappings[0]
                for key in ("data", "items", "bars", "results", "list")
            ):
                value = mappings[0]
                continue
            return mappings
        break
    return ()


def _first_row(response: object) -> Mapping[str, object] | None:
    rows = _rows(response)
    return rows[0] if rows else None


def _bar(row: Mapping[str, object]) -> HistoricalBar | None:
    try:
        opened = _decimal(row, "open", "o")
        high = _decimal(row, "high", "h")
        low = _decimal(row, "low", "l")
        close = _decimal(row, "close", "c")
        timestamp = _timestamp(row)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if min(opened, high, low, close) <= 0 or high < max(opened, close, low):
        return None
    if low > min(opened, close, high):
        return None
    volume = _optional_decimal(row, "volume", "v")
    return HistoricalBar(timestamp, opened, high, low, close, volume)


def _decimal(row: Mapping[str, object], *keys: str) -> Decimal:
    value = _value(row, *keys)
    return Decimal(str(value))


def _optional_decimal(row: Mapping[str, object], *keys: str) -> Decimal | None:
    value = _value(row, *keys)
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _value(row: Mapping[str, object], *keys: str) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    return next((lowered[key.lower()] for key in keys if key.lower() in lowered), None)


def _timestamp(row: Mapping[str, object]) -> datetime:
    value = _value(row, "timestamp", "time", "t", "date")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) or str(value).isdigit():
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = ["ChartMarketData", "ChartMarketDataService", "HistoricalBar"]
