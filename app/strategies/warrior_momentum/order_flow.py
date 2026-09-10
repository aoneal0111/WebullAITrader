"""Bounded, optional Webull footprint evidence for authorized execution."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Mapping


class OrderFlowClassification(StrEnum):
    STRONG_ACCUMULATION = "STRONG_ACCUMULATION"
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    DISTRIBUTION = "DISTRIBUTION"
    STRONG_DISTRIBUTION = "STRONG_DISTRIBUTION"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class OrderFlowCapabilities:
    footprint: bool
    capital_flow: bool
    streaming: bool = False


def detect_order_flow_capabilities(client: object) -> OrderFlowCapabilities:
    """Inspect an injected SDK client without making a network request."""
    market_data = getattr(client, "market_data", None)
    fundamentals = getattr(client, "fundamentals", None)
    return OrderFlowCapabilities(
        callable(getattr(market_data, "get_footprint", None)),
        callable(getattr(fundamentals, "get_capital_flow", None)),
        False,
    )


@dataclass(frozen=True, slots=True)
class OrderFlowSnapshot:
    observed_at: datetime
    symbol: str
    buy_volume: Decimal | None = None
    sell_volume: Decimal | None = None
    delta: Decimal | None = None
    cumulative_delta: Decimal | None = None
    total_volume: Decimal | None = None
    price: Decimal | None = None
    source: str = "WEBULL_FOOTPRINT"


@dataclass(frozen=True, slots=True)
class CapitalFlowSnapshot:
    observed_at: datetime
    symbol: str
    net_inflow: Decimal | None = None
    inflow: Decimal | None = None
    outflow: Decimal | None = None
    large_inflow: Decimal | None = None
    large_outflow: Decimal | None = None
    extra_large_inflow: Decimal | None = None
    extra_large_outflow: Decimal | None = None
    source: str = "WEBULL_CAPITAL_FLOW"


@dataclass(frozen=True, slots=True)
class OrderFlowAssessment:
    classification: OrderFlowClassification
    evaluated_at: datetime
    symbol: str
    delta: Decimal | None = None
    rolling_delta: Decimal | None = None
    buy_volume: Decimal | None = None
    sell_volume: Decimal | None = None
    fresh: bool = True
    reasons: tuple[str, ...] = ()

    @property
    def supports_pursuit(self) -> bool:
        return self.classification in {
            OrderFlowClassification.STRONG_ACCUMULATION,
            OrderFlowClassification.SUPPORTIVE,
        }

    @property
    def contradicts_pursuit(self) -> bool:
        return self.classification in {
            OrderFlowClassification.DISTRIBUTION,
            OrderFlowClassification.STRONG_DISTRIBUTION,
        }


def _decimal(value: object | None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _value(row: Mapping[str, object], *names: str) -> object | None:
    return next((row[name] for name in names if row.get(name) is not None), None)


def _rows(response: object) -> list[Mapping[str, object]]:
    if isinstance(response, Mapping):
        for name in ("data", "items", "list", "rows", "records"):
            candidate = response.get(name)
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, Mapping)]
        return [response]
    if isinstance(response, list):
        return [row for row in response if isinstance(row, Mapping)]
    return []


def normalize_footprint_response(
    response: object, *, symbol: str, observed_at: datetime,
) -> tuple[OrderFlowSnapshot, ...]:
    """Normalize only explicitly supplied provider flow values.

    The official SDK returns the endpoint response as an untyped object, so
    aliases are intentionally narrow and missing fields remain ``None``.
    """
    normalized_symbol = symbol.strip().upper()
    snapshots: list[OrderFlowSnapshot] = []
    for row in _rows(response):
        row_time = _value(row, "observed_at", "timestamp", "time", "date")
        timestamp = observed_at
        if isinstance(row_time, datetime) and row_time.tzinfo is not None:
            timestamp = row_time
        buy = _decimal(_value(row, "buy_volume", "buyVolume", "buy_vol"))
        sell = _decimal(_value(row, "sell_volume", "sellVolume", "sell_vol"))
        delta = _decimal(_value(row, "delta", "volume_delta", "volumeDelta"))
        if delta is None and buy is not None and sell is not None:
            delta = buy - sell
        snapshots.append(OrderFlowSnapshot(
            observed_at=timestamp,
            symbol=normalized_symbol,
            buy_volume=buy,
            sell_volume=sell,
            delta=delta,
            cumulative_delta=_decimal(_value(row, "cumulative_delta", "cumulativeDelta")),
            total_volume=_decimal(_value(row, "total_volume", "totalVolume", "volume")),
            price=_decimal(_value(row, "price", "price_level", "priceLevel")),
        ))
    return tuple(snapshots)


def normalize_capital_flow_response(
    response: object, *, symbol: str, observed_at: datetime,
) -> tuple[CapitalFlowSnapshot, ...]:
    """Normalize provider capital-flow tiers separately from footprint delta."""
    snapshots: list[CapitalFlowSnapshot] = []
    for row in _rows(response):
        snapshots.append(CapitalFlowSnapshot(
            observed_at=observed_at,
            symbol=symbol.strip().upper(),
            net_inflow=_decimal(_value(row, "net_inflow", "netInflow")),
            inflow=_decimal(_value(row, "inflow", "total_inflow", "totalInflow")),
            outflow=_decimal(_value(row, "outflow", "total_outflow", "totalOutflow")),
            large_inflow=_decimal(_value(row, "large_inflow", "largeInflow")),
            large_outflow=_decimal(_value(row, "large_outflow", "largeOutflow")),
            extra_large_inflow=_decimal(_value(row, "extra_large_inflow", "extraLargeInflow")),
            extra_large_outflow=_decimal(_value(row, "extra_large_outflow", "extraLargeOutflow")),
        ))
    return tuple(snapshots)


class OrderFlowMemory:
    """Latest snapshot plus a bounded completed-interval window."""

    def __init__(self, *, maximum_snapshots: int = 8) -> None:
        if maximum_snapshots <= 0:
            raise ValueError("maximum_snapshots must be positive")
        self._snapshots: deque[OrderFlowSnapshot] = deque(maxlen=maximum_snapshots)

    @property
    def snapshots(self) -> tuple[OrderFlowSnapshot, ...]:
        return tuple(self._snapshots)

    def add(self, snapshot: OrderFlowSnapshot) -> None:
        self._snapshots.append(snapshot)

    def assess(
        self, *, evaluated_at: datetime, maximum_age_seconds: Decimal,
    ) -> OrderFlowAssessment:
        if not self._snapshots:
            return OrderFlowAssessment(
                OrderFlowClassification.UNAVAILABLE, evaluated_at, "",
                fresh=False, reasons=("NO_ORDER_FLOW",),
            )
        latest = self._snapshots[-1]
        age = Decimal(str((evaluated_at - latest.observed_at).total_seconds()))
        fresh = age >= 0 and age <= maximum_age_seconds
        values = tuple(item.delta for item in self._snapshots if item.delta is not None)
        rolling = None if not values else sum(values, Decimal("0"))
        if not fresh or latest.delta is None:
            return OrderFlowAssessment(
                OrderFlowClassification.UNAVAILABLE, evaluated_at, latest.symbol,
                delta=latest.delta, rolling_delta=rolling, buy_volume=latest.buy_volume,
                sell_volume=latest.sell_volume, fresh=False,
                reasons=("ORDER_FLOW_STALE" if not fresh else "DELTA_UNAVAILABLE",),
            )
        if latest.delta > 0 and rolling is not None and rolling > 0:
            classification = (
                OrderFlowClassification.STRONG_ACCUMULATION
                if len(values) > 1 and all(value > 0 for value in values[-2:])
                else OrderFlowClassification.SUPPORTIVE
            )
        elif latest.delta < 0 and rolling is not None and rolling < 0:
            classification = (
                OrderFlowClassification.STRONG_DISTRIBUTION
                if len(values) > 1 and all(value < 0 for value in values[-2:])
                else OrderFlowClassification.DISTRIBUTION
            )
        else:
            classification = OrderFlowClassification.NEUTRAL
        return OrderFlowAssessment(
            classification, evaluated_at, latest.symbol, latest.delta, rolling,
            latest.buy_volume, latest.sell_volume, True,
            (classification.value,),
        )


__all__ = [
    "CapitalFlowSnapshot", "OrderFlowAssessment", "OrderFlowCapabilities",
    "OrderFlowClassification", "OrderFlowMemory", "OrderFlowSnapshot",
    "detect_order_flow_capabilities", "normalize_capital_flow_response",
    "normalize_footprint_response",
]
