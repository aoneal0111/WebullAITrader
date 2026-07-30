from __future__ import annotations
from app.webull.trading_sessions import resolve_webull_trading_session
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.broker_protocol.models import *
from app.webull.errors import SerializationError


def order_request_payload(
    order: BrokerOrderRequest,
    account_id: str,
    now=None,
) -> dict[str, object]:
    order_type_map = {
        BrokerOrderType.MARKET: "MARKET",
        BrokerOrderType.LIMIT: "LIMIT",
        BrokerOrderType.STOP: "STOP_LOSS",
        BrokerOrderType.STOP_LIMIT: "STOP_LOSS_LIMIT",
    }

    new_order: dict[str, str] = {
    "client_order_id": order.client_order_id,
    "combo_type": "NORMAL",
    "instrument_type": "EQUITY",
    "entrust_type": "QTY",
    "support_trading_session": resolve_webull_trading_session(
        order.trading_session,
        now,
    ),
    "symbol": order.symbol.upper(),
    "market": "US",
    "side": order.side.value,
    "order_type": order_type_map[order.order_type],
    "quantity": str(order.quantity),
    "time_in_force": order.time_in_force.value,
}

    if order.limit_price is not None:
        new_order["limit_price"] = str(order.limit_price)

    if order.stop_price is not None:
        new_order["stop_price"] = str(order.stop_price)

    return {
        "account_id": account_id,
        "new_orders": [new_order],
    }


def parse_order(
    value,
    request: BrokerOrderRequest | None = None,
) -> BrokerOrder:
    try:
        if isinstance(value, list):
            if not value:
                value = {}
            else:
                value = value[0]

        if isinstance(value, dict):
            for key in ("data", "orders", "new_orders", "result"):
                wrapped = value.get(key)

                if isinstance(wrapped, list) and wrapped:
                    value = wrapped[0]
                    break

                if isinstance(wrapped, dict):
                    value = wrapped
                    break

        if not isinstance(value, dict):
            raise TypeError("order response must be an object")

        request_client_order_id = (
            request.client_order_id if request is not None else None
        )
        request_symbol = request.symbol if request is not None else None
        request_side = request.side.value if request is not None else None
        request_order_type = (
            request.order_type.value if request is not None else None
        )
        request_quantity = request.quantity if request is not None else None
        request_limit_price = (
            request.limit_price if request is not None else None
        )
        request_stop_price = (
            request.stop_price if request is not None else None
        )
        request_time_in_force = (
            request.time_in_force.value if request is not None else None
        )

        raw_status = str(
            value.get("status")
            or value.get("order_status")
            or "ACKNOWLEDGED"
        ).upper()

        status_aliases = {
            "PENDING": "SUBMITTED",
            "PENDING_NEW": "SUBMITTED",
            "PLACED": "SUBMITTED",
            "WORKING": "ACKNOWLEDGED",
            "OPEN": "ACKNOWLEDGED",
            "ACTIVE": "ACKNOWLEDGED",
            "PARTIAL_FILLED": "PARTIALLY_FILLED",
            "PARTIALLYFILLED": "PARTIALLY_FILLED",
            "CANCELED": "CANCELLED",
        }

        status = LiveOrderStatus(
            status_aliases.get(raw_status, raw_status)
        )

        broker_order_id = (
            value.get("order_id")
            or value.get("broker_order_id")
            or value.get("orderId")
            or request_client_order_id
        )

        client_order_id = (
            value.get("client_order_id")
            or value.get("clientOrderId")
            or request_client_order_id
            or broker_order_id
        )

        symbol = (
            value.get("symbol")
            or value.get("ticker")
            or request_symbol
        )
        side = value.get("side") or value.get("action") or request_side
        order_type = (
            value.get("order_type")
            or value.get("orderType")
            or request_order_type
        )
        quantity = (
            value.get("quantity")
            if value.get("quantity") is not None
            else value.get("total_quantity")
        )
        if quantity is None:
            quantity = request_quantity

        time_in_force = (
            value.get("time_in_force")
            or value.get("timeInForce")
            or request_time_in_force
        )

        required = {
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "time_in_force": time_in_force,
        }
        missing = [name for name, item in required.items() if item is None]
        if missing:
            raise ValueError(
                "missing required order fields: " + ", ".join(missing)
            )

        return BrokerOrder(
            str(broker_order_id),
            str(client_order_id),
            str(symbol).upper(),
            LiveSide(str(side).upper()),
            LiveOrderType(str(order_type).upper()),
            _d(quantity),
            _d(
                value.get("filled_quantity")
                if value.get("filled_quantity") is not None
                else value.get("filled_qty", "0")
            ),
            _optional(
                value.get("limit_price")
                if "limit_price" in value
                else value.get("limitPrice", request_limit_price)
            ),
            _optional(
                value.get("stop_price")
                if "stop_price" in value
                else value.get("stopPrice", request_stop_price)
            ),
            TimeInForce(str(time_in_force).upper()),
            status,
            _dt(
                value.get("updated_timestamp")
                or value.get("update_time")
                or value.get("place_time_at")
            ) if (
                value.get("updated_timestamp")
                or value.get("update_time")
                or value.get("place_time_at")
            ) else datetime.now(timezone.utc),
        )

    except Exception as exc:
        if isinstance(exc, SerializationError):
            raise
        raise SerializationError(
            "malformed Webull order response"
        ) from exc


def parse_position(value) -> BrokerPosition:
    try:
        return BrokerPosition(
            str(value["symbol"]).upper(),
            _d(value["quantity"]),
            _d(
                value.get(
                    "average_price",
                    value.get("cost_price", "0"),
                )
            ),
            _optional(value.get("market_value")),
        )
    except Exception as exc:
        raise SerializationError(
            "malformed Webull position response"
        ) from exc


def parse_cash(value) -> BrokerCash:
    try:
        if not isinstance(value, dict):
            raise TypeError("cash response must be an object")

        currency_assets = value.get("account_currency_assets")

        if isinstance(currency_assets, list) and currency_assets:
            usd_asset = next(
                (
                    item
                    for item in currency_assets
                    if isinstance(item, dict)
                    and str(
                        item.get("currency", "")
                    ).upper() == "USD"
                ),
                None,
            )

            asset = usd_asset or next(
                (
                    item
                    for item in currency_assets
                    if isinstance(item, dict)
                ),
                None,
            )

            if asset is None:
                raise ValueError(
                    "no valid currency asset found"
                )

            return BrokerCash(
                settled_cash=_d(
                    asset.get(
                        "settled_cash",
                        asset.get("cash_balance", "0"),
                    )
                ),
                unsettled_cash=_d(
                    asset.get("unsettled_cash", "0")
                ),
                currency=str(
                    asset.get("currency", "USD")
                ).upper(),
                buying_power=_optional_decimal(
                    value,
                    asset,
                    "buying_power",
                    "buyingPower",
                    "available_funds",
                    "availableFunds",
                ),
                equity=_optional_decimal(
                    value,
                    asset,
                    "equity",
                    "net_liquidation",
                    "netLiquidation",
                    "total_asset",
                    "totalAsset",
                    "total_value",
                ),
            )

        return BrokerCash(
            settled_cash=_d(
                value.get(
                    "settled_cash",
                    value.get("total_cash_balance", "0"),
                )
            ),
            unsettled_cash=_d(
                value.get("unsettled_cash", "0")
            ),
            currency=str(
                value.get(
                    "currency",
                    value.get(
                        "total_asset_currency",
                        "USD",
                    ),
                )
            ).upper(),
            buying_power=_optional_decimal(
                value,
                "buying_power",
                "buyingPower",
                "available_funds",
                "availableFunds",
            ),
            equity=_optional_decimal(
                value,
                "equity",
                "net_liquidation",
                "netLiquidation",
                "total_asset",
                "totalAsset",
                "total_value",
            ),
        )

    except Exception as exc:
        if isinstance(exc, SerializationError):
            raise
        raise SerializationError(
            "malformed Webull cash response"
        ) from exc

def parse_fill(value) -> BrokerFill:
    try: return BrokerFill(str(value["fill_id"]), str(value.get("order_id") or value["broker_order_id"]), _d(value["quantity"]), _d(value["price"]), _dt(value.get("timestamp") or value.get("trade_time")))
    except Exception as exc: raise SerializationError("malformed Webull fill response") from exc
def _d(value):
    result = Decimal(str(value))
    if not result.is_finite(): raise SerializationError("non-finite broker Decimal")
    return result
def _optional(value): return None if value is None or value == "" else _d(value)


def _optional_decimal(value, *keys):
    sources = (value,)
    if keys and isinstance(keys[0], dict):
        sources = (value, keys[0])
        keys = keys[1:]
    for source in sources:
        for key in keys:
            if key in source:
                return _optional(source[key])
    return None


def _dt(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None: raise SerializationError("broker timestamp is naive")
    return result
