"""Fail-closed execution-time quote confirmation contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from app.webull.sdk_market_data import LazyOfficialDataClient, _response_rows

from .execution_quote_provenance import (
    ExecutionQuoteProvenanceSink,
    NOOP_EXECUTION_QUOTE_PROVENANCE,
)


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
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 provenance_sink: ExecutionQuoteProvenanceSink = NOOP_EXECUTION_QUOTE_PROVENANCE,
                 provenance_clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        if not isinstance(client, LazyOfficialDataClient):
            raise TypeError("client must be a LazyOfficialDataClient")
        self._client = client
        self._category = category
        self._clock = clock
        self._provenance_sink = provenance_sink
        self._provenance_clock = provenance_clock
        self._provenance_context: ContextVar[dict[str, object] | None] = ContextVar(
            f"execution_quote_provenance_{id(self)}", default=None,
        )

    def __call__(self, symbol: str) -> ExecutionQuoteSnapshot | None:
        normalized = symbol.strip().upper()
        if not normalized:
            return None
        if not bool(getattr(self._provenance_sink, "enabled", False)):
            return self._request_without_provenance(normalized)
        try:
            context: dict[str, object] = {
                "request_id": uuid4().hex,
                "symbol": normalized,
                "request_timestamp": self._safe_provenance_now(),
            }
            self._provenance_context.set(context)
            self._record_provenance("EXECUTION_QUOTE_REQUESTED", **context)
        except Exception:
            return self._request_without_provenance(normalized)
        try:
            market_data = getattr(self._client.get(), "market_data")
            response = market_data.get_snapshot(
                symbols=[normalized], category=self._category,
                extend_hour_required=True,
            )
            rows = _response_rows(response)
            row = rows[0] if rows else None
            returned_at = self._clock()
            context["snapshot_return_timestamp"] = returned_at
            if row is None:
                self._record_provenance(
                    "RAW_PROVIDER", **context, raw_row_type="MISSING",
                    raw_row_category=self._category, parse_status="UNAVAILABLE",
                )
                return None
            self._record_raw_row(row, context)
            parsed = parse_execution_quote(
                normalized, row, confirmed_at=returned_at,
            )
            if parsed is None:
                self._record_provenance(
                    "PARSED_EXECUTION_QUOTE", **context, parse_status="UNAVAILABLE",
                )
                return None
            self._record_provenance(
                "PARSED_EXECUTION_QUOTE", **context, parse_status="AVAILABLE",
                parsed_last=parsed.last, parsed_bid=parsed.bid, parsed_ask=parsed.ask,
                parsed_last_timestamp=parsed.last_timestamp,
                parsed_bid_timestamp=parsed.bid_timestamp,
                parsed_ask_timestamp=parsed.ask_timestamp,
            )
            return parsed
        except Exception as exc:
            self._record_provenance(
                "RAW_PROVIDER_UNAVAILABLE", **context,
                parse_status="UNAVAILABLE", exception_class=type(exc).__name__,
            )
            return None

    def _request_without_provenance(
        self, normalized: str,
    ) -> ExecutionQuoteSnapshot | None:
        """Preserve the original request path exactly when diagnostics are off."""

        try:
            market_data = getattr(self._client.get(), "market_data")
            response = market_data.get_snapshot(
                symbols=[normalized], category=self._category,
                extend_hour_required=True,
            )
            rows = _response_rows(response)
            row = rows[0] if rows else None
            return None if row is None else parse_execution_quote(
                normalized, row, confirmed_at=self._clock(),
            )
        except Exception:
            return None

    def record_decision(
        self,
        *,
        evaluated_at: datetime,
        snapshot: ExecutionQuoteSnapshot | None,
        outcome: str,
        rejection_reason: str | None,
    ) -> None:
        """Record the final decision for the current synchronous request."""

        try:
            context = self._provenance_context.get()
            if context is None:
                return
            details: dict[str, object] = {
                **context,
                "evaluation_timestamp": evaluated_at,
                "final_outcome": outcome,
            }
            if rejection_reason is not None:
                details["rejection_reason"] = rejection_reason
            if snapshot is not None:
                details.update({
                    "calculated_last_age_seconds": Decimal(str(
                        (evaluated_at - snapshot.last_timestamp).total_seconds()
                    )),
                    "calculated_bid_age_seconds": Decimal(str(
                        (evaluated_at - snapshot.bid_timestamp).total_seconds()
                    )),
                    "calculated_ask_age_seconds": Decimal(str(
                        (evaluated_at - snapshot.ask_timestamp).total_seconds()
                    )),
                })
            self._record_provenance("FINAL_DECISION", **details)
        except Exception:
            return None

    def _safe_provenance_now(self) -> datetime | None:
        try:
            return self._provenance_clock()
        except Exception:
            return None

    def _record_raw_row(
        self, row: Mapping[str, object], context: Mapping[str, object],
    ) -> None:
        try:
            lowered = {str(key).lower(): value for key, value in row.items()}
            self._record_provenance(
                "RAW_PROVIDER", **context, raw_row_type="MAPPING",
                raw_row_category=self._category, raw_symbol=lowered.get("symbol"),
                raw_price=lowered.get("price"), raw_bid=lowered.get("bid"),
                raw_ask=lowered.get("ask"),
                raw_last_trade_time=lowered.get("last_trade_time"),
                raw_quote_time=lowered.get("quote_time"),
                has_bid_time="bid_time" in lowered,
                has_ask_time="ask_time" in lowered,
                has_update_time="update_time" in lowered,
                has_timestamp="timestamp" in lowered,
                has_trade_time="trade_time" in lowered,
            )
        except Exception:
            return None

    def _record_provenance(self, event_type: str, **details: object) -> None:
        try:
            self._provenance_sink.record({
                "event_type": event_type,
                "timestamp": self._safe_provenance_now(),
                **details,
            })
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
