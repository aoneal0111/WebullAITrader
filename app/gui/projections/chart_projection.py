"""Projection from REST market facts into the immutable chart model."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import logging
from typing import Mapping

from app.gui.models.chart import ChartCandle, ChartViewSnapshot
from app.services.chart_market_data import ChartMarketData, ChartMarketDataService


_LOGGER = logging.getLogger("atlas.gui.chart")


class ChartProjection:
    def __init__(self, service: ChartMarketDataService) -> None:
        if not isinstance(service, ChartMarketDataService):
            raise TypeError("service must be a ChartMarketDataService")
        self._service = service

    def request(self, symbol: str, timeframe: str = "1D") -> ChartViewSnapshot:
        normalized = symbol.strip().upper() if isinstance(symbol, str) else ""
        _LOGGER.info(
            "operation=chart_projection_request symbol=%s timeframe=%s",
            normalized or "--",
            timeframe,
        )
        result = self._service.load(normalized, timeframe)
        model = project_chart_model(result)
        _LOGGER.info(
            "operation=chart_model_update symbol=%s candle_count=%d status=%s reason=%s",
            model.symbol,
            len(model.candles),
            "ready" if model.candles else "empty",
            model.message,
        )
        return model


def project_chart_model(data: ChartMarketData) -> ChartViewSnapshot:
    candles = tuple(
        ChartCandle(
            item.timestamp,
            item.open,
            item.high,
            item.low,
            item.close,
            item.volume,
        )
        for item in data.bars
    )
    latest = candles[-1] if candles else None
    facts = _facts(data.snapshot, data.quote)
    opened = _number(facts, "open", "openPrice") or (latest.open if latest else None)
    high = _number(facts, "high", "highPrice") or (latest.high if latest else None)
    low = _number(facts, "low", "lowPrice") or (latest.low if latest else None)
    close = _number(facts, "last", "lastPrice", "price", "close") or (latest.close if latest else None)
    previous = _number(facts, "prevClose", "previousClose", "preClose")
    if previous is None:
        previous = candles[-2].close if len(candles) > 1 else opened
    change = close - previous if close is not None and previous is not None else None
    change_percent = (
        change / previous * Decimal("100")
        if change is not None and previous not in (None, Decimal("0")) else None
    )
    if candles:
        message = f"Loaded {len(candles)} historical candles through REST."
    else:
        message = data.error or "No historical candles were returned by REST."
    return ChartViewSnapshot(
        symbol=data.symbol,
        timeframe=data.timeframe,
        market_status="UNKNOWN",
        message=message,
        candles=candles,
        open=opened,
        high=high,
        low=low,
        close=close,
        change=change,
        change_percent=change_percent,
        volume=_number(facts, "volume", "totalVolume") or (latest.volume if latest else None),
        instrument_name=_text(facts, "name", "instrumentName", "companyName", "tickerName"),
        previous_close=previous,
        bid=_number(facts, "bid", "bidPrice"),
        ask=_number(facts, "ask", "askPrice"),
        bid_size=_number(facts, "bidSize", "bidVolume"),
        ask_size=_number(facts, "askSize", "askVolume"),
        turnover=_number(facts, "turnover", "amount", "tradeValue"),
        session=_text(facts, "marketStatus", "session", "status"),
    )


def _facts(*sources: Mapping[str, object] | None) -> dict[str, object]:
    result: dict[str, object] = {}
    for source in sources:
        if source:
            result.update(source)
    return result


def _text(row: Mapping[str, object], *keys: str) -> str | None:
    lowered = {str(key).lower(): value for key, value in row.items()}
    value = next((lowered[key.lower()] for key in keys if key.lower() in lowered), None)
    return str(value).strip() if value not in (None, "") else None


def _number(row: Mapping[str, object], *keys: str) -> Decimal | None:
    lowered = {str(key).lower(): value for key, value in row.items()}
    value = next(
        (lowered[key.lower()] for key in keys if key.lower() in lowered),
        None,
    )
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


__all__ = ["ChartProjection", "project_chart_model"]
