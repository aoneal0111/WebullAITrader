"""Durable PAPER execution snapshots and event replay."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.momentum_scanner import AssetClass
from app.operations.runtime import PaperRuntimeEvent, RuntimeDecision
from app.operations_core import OperationsOrder
from app.paper_trading.fill_models import Fill
from app.paper_trading.models import PaperFill
from app.paper_trading.order_models import (
    OrderRequest, OrderSide, OrderStatus, OrderType, PaperOrder, TimeInForce,
)


SCHEMA_VERSION = 1


class DurablePaperExecutionStore:
    """PAPER-namespaced SQLite store for authoritative gateway events."""

    def __init__(self, path: str | Path, *, account_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY, event_type TEXT NOT NULL, payload TEXT NOT NULL);
            """
        )
        self._ensure_metadata("schema_version", str(SCHEMA_VERSION))
        self._ensure_metadata("environment", "PAPER")
        self._ensure_metadata("account_id", account_id)
        if self._metadata("environment") != "PAPER" or self._metadata("account_id") != account_id:
            raise ValueError("PAPER execution store identity mismatch")

    def close(self) -> None:
        self._connection.close()

    def persist(self, event: PaperRuntimeEvent, order: PaperOrder | None = None) -> None:
        payload = _event_payload(event)
        with self._connection:
            if order is not None:
                self._connection.execute(
                    "INSERT INTO orders(order_id,payload) VALUES(?,?) ON CONFLICT(order_id) DO UPDATE SET payload=excluded.payload",
                    (order.order_id, json.dumps(_order_payload(order), sort_keys=True)),
                )
            self._connection.execute(
                "INSERT OR IGNORE INTO events(sequence,event_type,payload) VALUES(?,?,?)",
                (event.sequence, event.event_type, json.dumps(payload, sort_keys=True)),
            )

    def orders(self) -> tuple[PaperOrder, ...]:
        rows = self._connection.execute("SELECT payload FROM orders ORDER BY order_id").fetchall()
        return tuple(_order_from_payload(json.loads(row[0])) for row in rows)

    def events(self) -> tuple[PaperRuntimeEvent, ...]:
        rows = self._connection.execute("SELECT payload FROM events ORDER BY sequence").fetchall()
        return tuple(_event_from_payload(json.loads(row[0])) for row in rows)

    def _metadata(self, key: str) -> str:
        row = self._connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ValueError(f"missing PAPER execution metadata: {key}")
        return row[0]

    def _ensure_metadata(self, key: str, value: str) -> None:
        existing = self._connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if existing is None:
            self._connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))
        elif key == "schema_version" and existing[0] != value:
            raise ValueError("unsupported PAPER execution store schema")


def _order_payload(order: PaperOrder) -> dict:
    return {
        "order_id": order.order_id, "status": order.status.value,
        "created_at": order.created_at.isoformat(), "updated_at": order.updated_at.isoformat(),
        "filled_quantity": str(order.filled_quantity),
        "average_fill_price": None if order.average_fill_price is None else str(order.average_fill_price),
        "rejection_reason": order.rejection_reason,
        "request": {
            "symbol": order.request.symbol, "asset_class": order.request.asset_class.value,
            "side": order.request.side.value, "order_type": order.request.order_type.value,
            "quantity": str(order.request.quantity), "time_in_force": order.request.time_in_force.value,
            "limit_price": None if order.request.limit_price is None else str(order.request.limit_price),
            "stop_price": None if order.request.stop_price is None else str(order.request.stop_price),
            "client_order_id": order.request.client_order_id,
            "strategy_lifecycle_id": order.request.strategy_lifecycle_id,
        },
        "fills": [_fill_payload(fill) for fill in order.fills],
    }


def _order_from_payload(value: dict) -> PaperOrder:
    request = value["request"]
    return PaperOrder(
        order_id=value["order_id"], status=OrderStatus(value["status"]),
        created_at=datetime.fromisoformat(value["created_at"]), updated_at=datetime.fromisoformat(value["updated_at"]),
        filled_quantity=Decimal(value["filled_quantity"]),
        average_fill_price=None if value["average_fill_price"] is None else Decimal(value["average_fill_price"]),
        rejection_reason=value["rejection_reason"],
        request=OrderRequest(
            symbol=request["symbol"], asset_class=AssetClass(request["asset_class"]),
            side=OrderSide(request["side"]), order_type=OrderType(request["order_type"]),
            quantity=Decimal(request["quantity"]), time_in_force=TimeInForce(request["time_in_force"]),
            limit_price=None if request["limit_price"] is None else Decimal(request["limit_price"]),
            stop_price=None if request["stop_price"] is None else Decimal(request["stop_price"]),
            client_order_id=request["client_order_id"],
            strategy_lifecycle_id=request.get("strategy_lifecycle_id"),
        ),
        fills=tuple(_fill_from_payload(fill) for fill in value["fills"]),
    )


def _fill_payload(fill: Fill) -> dict:
    return {"fill_id": fill.fill_id, "order_id": fill.order_id, "quantity": str(fill.quantity), "price": str(fill.price), "timestamp": fill.timestamp.isoformat(), "commission": str(fill.commission), "slippage": str(fill.slippage), "venue": fill.venue, "liquidity_flag": fill.liquidity_flag}


def _fill_from_payload(value: dict) -> Fill:
    return Fill(fill_id=value["fill_id"], order_id=value["order_id"], quantity=Decimal(value["quantity"]), price=Decimal(value["price"]), timestamp=datetime.fromisoformat(value["timestamp"]), commission=Decimal(value["commission"]), slippage=Decimal(value["slippage"]), venue=value["venue"], liquidity_flag=value["liquidity_flag"])


def _event_payload(event: PaperRuntimeEvent) -> dict:
    return {"sequence": event.sequence, "event_type": event.event_type, "timestamp": event.timestamp.isoformat(), "message": event.message, "cycle": event.cycle, "symbol": event.symbol, "source": event.source, "order": None if event.order is None else {"order_id": event.order.order_id, "symbol": event.order.symbol, "side": event.order.side, "quantity": event.order.quantity, "status": event.order.status, "updated_at": event.order.updated_at.isoformat()}, "fill": None if event.fill is None else {"request_id": event.fill.request_id, "symbol": event.fill.symbol, "side": event.fill.side, "quantity": str(event.fill.quantity), "fill_price": str(event.fill.fill_price), "notional": str(event.fill.notional), "realized_pnl": str(event.fill.realized_pnl), "timestamp": event.fill.timestamp.isoformat()}}


def _event_from_payload(value: dict) -> PaperRuntimeEvent:
    order = value["order"]
    fill = value["fill"]
    return PaperRuntimeEvent(
        sequence=value["sequence"], timestamp=datetime.fromisoformat(value["timestamp"]), event_type=value.get("event_type", "ORDER_REPLAY"), message=value["message"], cycle=value["cycle"], symbol=value["symbol"], source="paper-execution-replay",
        order=None if order is None else OperationsOrder(order_id=order["order_id"], symbol=order["symbol"], side=order["side"], quantity=order["quantity"], status=order["status"], updated_at=datetime.fromisoformat(order["updated_at"])),
        fill=None if fill is None else PaperFill(request_id=fill["request_id"], symbol=fill["symbol"], side=fill["side"], quantity=Decimal(fill["quantity"]), fill_price=Decimal(fill["fill_price"]), notional=Decimal(fill["notional"]), realized_pnl=Decimal(fill["realized_pnl"]), timestamp=datetime.fromisoformat(fill["timestamp"])),
    )


__all__ = ["DurablePaperExecutionStore", "SCHEMA_VERSION"]
