from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.broker.mcp_client import AccountDataClient
from app.broker.models import AccountSnapshot, Balance, Position
from app.exceptions import MCPResponseError


class AccountSnapshotService:
    def __init__(self, client: AccountDataClient) -> None:
        self._client = client

    async def retrieve(self) -> tuple[AccountSnapshot, ...]:
        accounts = await self._client.get_accounts()
        snapshots: list[AccountSnapshot] = []
        for account in accounts:
            account_id = _text(account, "account_id", "accountId", "id")
            if not account_id:
                raise MCPResponseError("Account response is missing an account ID")
            account_number = _text(account, "account_number", "accountNumber", "account_no")
            balance_data = await self._client.get_balance(account_id)
            position_data = await self._client.get_positions(account_id)
            snapshots.append(
                AccountSnapshot(
                    account_id=account_id,
                    account_number=account_number or account_id,
                    balance=_parse_balance(balance_data),
                    positions=tuple(_parse_position(item) for item in position_data),
                )
            )
        return tuple(snapshots)


def _parse_balance(data: dict[str, Any]) -> Balance:
    return Balance(
        total_value=_decimal(data, "total_value", "totalValue", "net_liquidation"),
        cash=_decimal(data, "cash", "cash_balance", "cashBalance"),
        buying_power=_decimal(data, "buying_power", "buyingPower"),
        currency=_text(data, "currency", "currency_code", "currencyCode") or None,
    )


def _parse_position(data: dict[str, Any]) -> Position:
    symbol = _text(data, "symbol", "ticker")
    if not symbol:
        instrument = data.get("instrument")
        if isinstance(instrument, dict):
            symbol = _text(instrument, "symbol", "ticker")
    if not symbol:
        raise MCPResponseError("Position response is missing a symbol")
    quantity = _decimal(data, "quantity", "qty", "position")
    if quantity is None:
        raise MCPResponseError(f"Position response is missing quantity for {symbol}")
    return Position(
        symbol=symbol,
        quantity=quantity,
        market_value=_decimal(data, "market_value", "marketValue"),
        average_price=_decimal(data, "average_price", "averagePrice", "cost_price"),
    )


def _text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _decimal(data: dict[str, Any], *keys: str) -> Decimal | None:
    raw = _text(data, *keys)
    if not raw:
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation as exc:
        raise MCPResponseError(f"Invalid numeric value for {keys[0]}") from exc
