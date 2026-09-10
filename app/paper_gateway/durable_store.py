"""Durable PAPER execution snapshots and event replay."""

from __future__ import annotations

import json
import sqlite3
from uuid import uuid4
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock

from app.momentum_scanner import AssetClass
from app.operations.runtime import PaperRuntimeEvent, RuntimeDecision
from app.operations_core import OperationsOrder
from app.paper_trading.fill_models import Fill
from app.paper_trading.models import PaperFill
from app.paper_trading.order_models import (
    OrderRequest, OrderSide, OrderStatus, OrderTerminalReason, OrderType,
    PaperOrder, TimeInForce,
)


SCHEMA_VERSION = 1
LEGACY_PAPER_CAMPAIGN_ID = "legacy-paper-campaign"
NO_ACTIVE_PAPER_CAMPAIGN_ID = "no-active-paper-campaign"
DEFAULT_ATLAS_PAPER_STARTING_CASH = Decimal("10000")
DEFAULT_BUSY_TIMEOUT_SECONDS = 1.0


ConnectionFactory = Callable[..., sqlite3.Connection]


class DurablePaperExecutionStore:
    """Thread-safe PAPER store using one same-thread connection per operation.

    PAPER commands can originate on either the desktop command thread or the
    market-data processing thread.  Connections are therefore deliberately
    short lived: the thread entering an operation opens, uses, and closes its
    connection while ``_lock`` supplies one logical writer and coordinates
    shutdown.  No SQLite connection crosses a thread boundary.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        account_id: str,
        connection_factory: ConnectionFactory = sqlite3.connect,
        busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        if busy_timeout_seconds < 0:
            raise ValueError("busy timeout must be nonnegative")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._account_id = account_id
        self._connection_factory = connection_factory
        self._busy_timeout_seconds = float(busy_timeout_seconds)
        self._lock = RLock()
        self._closed = False
        with self._lock:
            connection = self._open_connection(initialize=True)
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY, event_type TEXT NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS paper_campaigns(
                        campaign_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        reason TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS paper_campaign_operations(
                        operation_key TEXT PRIMARY KEY,
                        campaign_id TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS paper_campaign_capital(
                        campaign_id TEXT PRIMARY KEY,
                        starting_equity TEXT NOT NULL,
                        starting_cash TEXT NOT NULL,
                        buying_power_multiplier TEXT NOT NULL
                    );
                    """
                )
                self._ensure_metadata(connection, "schema_version", str(SCHEMA_VERSION))
                self._ensure_metadata(connection, "environment", "PAPER")
                self._ensure_metadata(connection, "account_id", account_id)
                if (
                    self._metadata(connection, "environment") != "PAPER"
                    or self._metadata(connection, "account_id") != account_id
                ):
                    raise ValueError("PAPER execution store identity mismatch")
                self._ensure_initial_campaign(connection)
            finally:
                connection.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def persist(self, event: PaperRuntimeEvent, order: PaperOrder | None = None) -> None:
        self.persist_batch((event,), order)

    def persist_batch(
        self,
        events: Iterable[PaperRuntimeEvent],
        order: PaperOrder | None = None,
    ) -> None:
        event_values = tuple(events)
        if not event_values:
            raise ValueError("at least one PAPER runtime event is required")
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                campaign_id = self._active_campaign_id(connection)
                if campaign_id is None:
                    raise RuntimeError(
                        "no active PAPER campaign; start a new campaign explicitly"
                    )
                if order is not None:
                    connection.execute(
                        "INSERT INTO orders(order_id,payload) VALUES(?,?) ON CONFLICT(order_id) DO UPDATE SET payload=excluded.payload",
                        (order.order_id, json.dumps(_order_payload(order, campaign_id), sort_keys=True)),
                    )
                for event in event_values:
                    connection.execute(
                        "INSERT OR IGNORE INTO events(sequence,event_type,payload) VALUES(?,?,?)",
                        (
                            event.sequence,
                            event.event_type,
                            json.dumps(_event_payload(event, campaign_id), sort_keys=True),
                        ),
                    )
                connection.commit()
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
            finally:
                connection.close()

    def orders(self) -> tuple[PaperOrder, ...]:
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                rows = connection.execute(
                    "SELECT payload FROM orders ORDER BY order_id"
                ).fetchall()
                values = [json.loads(row[0]) for row in rows]
                return tuple(
                    _order_from_payload(value) for value in values
                    if _payload_campaign(value) == self.active_campaign_id
                )
            finally:
                connection.close()

    def events(self) -> tuple[PaperRuntimeEvent, ...]:
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                rows = connection.execute(
                    "SELECT payload FROM events ORDER BY sequence"
                ).fetchall()
                values = [json.loads(row[0]) for row in rows]
                # Validate every durable row before applying the active
                # campaign filter; corruption must fail closed even when the
                # row belongs to legacy history.
                parsed = tuple(_event_from_payload(value) for value in values)
                return tuple(
                    event for event, value in zip(parsed, values)
                    if _payload_campaign(value) == self.active_campaign_id
                )
            finally:
                connection.close()

    @property
    def active_campaign_id(self) -> str | None:
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                return self._active_campaign_id(connection)
            finally:
                connection.close()

    def historical_orders(self) -> tuple[PaperOrder, ...]:
        return self._all_orders()

    def historical_events(self) -> tuple[PaperRuntimeEvent, ...]:
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                rows = connection.execute("SELECT payload FROM events ORDER BY sequence").fetchall()
                return tuple(_event_from_payload(json.loads(row[0])) for row in rows)
            finally:
                connection.close()

    def campaigns(self) -> tuple[dict[str, str | None], ...]:
        """Return campaign metadata for audit/research, newest first."""
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                rows = connection.execute(
                    "SELECT campaign_id,status,started_at,ended_at,reason FROM paper_campaigns ORDER BY started_at"
                ).fetchall()
                if not rows and self._has_legacy_rows(connection):
                    rows = [
                        (
                            LEGACY_PAPER_CAMPAIGN_ID, "HISTORICAL", None, None,
                            "pre-campaign legacy data",
                        )
                    ]
                return tuple(
                    {
                        "campaign_id": row[0], "status": row[1],
                        "started_at": row[2], "ended_at": row[3],
                        "reason": row[4],
                    }
                    for row in rows
                )
            finally:
                connection.close()

    def active_campaign_capital(self) -> dict[str, Decimal] | None:
        """Return immutable starting-capital metadata for the active campaign."""
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                campaign_id = self._active_campaign_id(connection)
                if campaign_id is None:
                    return None
                row = connection.execute(
                    "SELECT starting_equity,starting_cash,buying_power_multiplier FROM paper_campaign_capital WHERE campaign_id=?",
                    (campaign_id,),
                ).fetchone()
                if row is None:
                    return {
                        "starting_equity": DEFAULT_ATLAS_PAPER_STARTING_CASH,
                        "starting_cash": DEFAULT_ATLAS_PAPER_STARTING_CASH,
                        "buying_power_multiplier": Decimal("1"),
                    }
                return {
                    "starting_equity": Decimal(row[0]),
                    "starting_cash": Decimal(row[1]),
                    "buying_power_multiplier": Decimal(row[2]),
                }
            finally:
                connection.close()

    def start_new_paper_campaign(
        self, *, operation_key: str, campaign_id: str | None = None,
        started_at: datetime | None = None, reason: str = "explicit-rollover",
        starting_equity: Decimal = DEFAULT_ATLAS_PAPER_STARTING_CASH,
        starting_cash: Decimal = DEFAULT_ATLAS_PAPER_STARTING_CASH,
        buying_power_multiplier: Decimal = Decimal("1"),
    ) -> str:
        """Create an auditable active campaign without rewriting old rows."""
        if not operation_key.strip():
            raise ValueError("operation_key is required")
        if not all(_valid_nonnegative(value) for value in (starting_equity, starting_cash)):
            raise ValueError("starting capital must be finite and nonnegative")
        if not _valid_positive(buying_power_multiplier):
            raise ValueError("buying_power_multiplier must be positive")
        campaign_id = campaign_id or f"paper-{uuid4().hex}"
        started_at = started_at or datetime.now().astimezone()
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                prior = connection.execute(
                    "SELECT campaign_id FROM paper_campaign_operations WHERE operation_key=?",
                    (operation_key,),
                ).fetchone()
                if prior is not None:
                    connection.commit()
                    return str(prior[0])
                active = self._active_campaign_id(connection)
                if active is not None:
                    connection.execute(
                        "UPDATE paper_campaigns SET status='ENDED', ended_at=? WHERE campaign_id=? AND status='ACTIVE'",
                        (started_at.isoformat(), active),
                    )
                if active is None and self._has_legacy_rows(connection):
                    connection.execute(
                        "INSERT OR IGNORE INTO paper_campaigns VALUES(?,?,?,?,?)",
                        (LEGACY_PAPER_CAMPAIGN_ID, "HISTORICAL", started_at.isoformat(), started_at.isoformat(), "pre-campaign legacy data"),
                    )
                connection.execute(
                    "INSERT INTO paper_campaigns VALUES(?,?,?,?,?)",
                    (campaign_id, "ACTIVE", started_at.isoformat(), None, reason),
                )
                connection.execute(
                    "INSERT INTO paper_campaign_capital VALUES(?,?,?,?)",
                    (campaign_id, str(starting_equity), str(starting_cash), str(buying_power_multiplier)),
                )
                self._set_metadata(connection, "active_campaign_id", campaign_id)
                connection.execute(
                    "INSERT INTO paper_campaign_operations VALUES(?,?,?)",
                    (operation_key, campaign_id, started_at.isoformat()),
                )
                connection.commit()
                return campaign_id
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _all_orders(self) -> tuple[PaperOrder, ...]:
        with self._lock:
            self._require_open()
            connection = self._open_connection()
            try:
                rows = connection.execute("SELECT payload FROM orders ORDER BY order_id").fetchall()
                return tuple(_order_from_payload(json.loads(row[0])) for row in rows)
            finally:
                connection.close()

    def _ensure_initial_campaign(self, connection: sqlite3.Connection) -> None:
        if self._metadata_optional(connection, "active_campaign_id") is not None:
            return
        if self._has_legacy_rows(connection):
            return
        now = datetime.now().astimezone().isoformat()
        campaign_id = f"paper-{uuid4().hex}"
        connection.execute("INSERT INTO paper_campaigns VALUES(?,?,?,?,?)", (campaign_id, "ACTIVE", now, None, "initial-empty-store"))
        connection.execute(
            "INSERT INTO paper_campaign_capital VALUES(?,?,?,?)",
            (campaign_id, str(DEFAULT_ATLAS_PAPER_STARTING_CASH), str(DEFAULT_ATLAS_PAPER_STARTING_CASH), "1"),
        )
        self._set_metadata(connection, "active_campaign_id", campaign_id)

    @staticmethod
    def _has_legacy_rows(connection: sqlite3.Connection) -> bool:
        return bool(connection.execute("SELECT 1 FROM events LIMIT 1").fetchone() or connection.execute("SELECT 1 FROM orders LIMIT 1").fetchone())

    @staticmethod
    def _metadata_optional(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    @classmethod
    def _active_campaign_id(cls, connection: sqlite3.Connection) -> str | None:
        return cls._metadata_optional(connection, "active_campaign_id")

    @staticmethod
    def _set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def _open_connection(self, *, initialize: bool = False) -> sqlite3.Connection:
        connection = self._connection_factory(
            str(self.path),
            timeout=self._busy_timeout_seconds,
            isolation_level=None,
        )
        try:
            if initialize:
                connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except Exception:
            connection.close()
            raise

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("PAPER execution store is closed")

    @staticmethod
    def _metadata(connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            raise ValueError(f"missing PAPER execution metadata: {key}")
        return row[0]

    @staticmethod
    def _ensure_metadata(
        connection: sqlite3.Connection, key: str, value: str
    ) -> None:
        existing = connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        if existing is None:
            connection.execute("INSERT INTO metadata VALUES(?,?)", (key, value))
        elif key == "schema_version" and existing[0] != value:
            raise ValueError("unsupported PAPER execution store schema")


def _order_payload(order: PaperOrder, campaign_id: str) -> dict:
    return {
        "paper_campaign_id": campaign_id,
        "order_id": order.order_id, "status": order.status.value,
        "created_at": order.created_at.isoformat(), "updated_at": order.updated_at.isoformat(),
        "filled_quantity": str(order.filled_quantity),
        "average_fill_price": None if order.average_fill_price is None else str(order.average_fill_price),
        "rejection_reason": order.rejection_reason,
        "terminal_reason": (
            None if order.terminal_reason is None else order.terminal_reason.value
        ),
        "request": {
            "symbol": order.request.symbol, "asset_class": order.request.asset_class.value,
            "side": order.request.side.value, "order_type": order.request.order_type.value,
            "quantity": str(order.request.quantity), "time_in_force": order.request.time_in_force.value,
            "limit_price": None if order.request.limit_price is None else str(order.request.limit_price),
            "stop_price": None if order.request.stop_price is None else str(order.request.stop_price),
            "client_order_id": order.request.client_order_id,
            "strategy_lifecycle_id": order.request.strategy_lifecycle_id,
            "structural_stop_price": (
                None
                if order.request.structural_stop_price is None
                else str(order.request.structural_stop_price)
            ),
            "execution_reason": order.request.execution_reason,
            "entry_valid_until": (
                None
                if order.request.entry_valid_until is None
                else order.request.entry_valid_until.isoformat()
            ),
            "metadata": dict(order.request.metadata),
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
        terminal_reason=(
            None
            if value.get("terminal_reason") is None
            else OrderTerminalReason(value["terminal_reason"])
        ),
        request=OrderRequest(
            symbol=request["symbol"], asset_class=AssetClass(request["asset_class"]),
            side=OrderSide(request["side"]), order_type=OrderType(request["order_type"]),
            quantity=Decimal(request["quantity"]), time_in_force=TimeInForce(request["time_in_force"]),
            limit_price=None if request["limit_price"] is None else Decimal(request["limit_price"]),
            stop_price=None if request["stop_price"] is None else Decimal(request["stop_price"]),
            client_order_id=request["client_order_id"],
            strategy_lifecycle_id=request.get("strategy_lifecycle_id"),
            structural_stop_price=(
                None
                if request.get("structural_stop_price") is None
                else Decimal(request["structural_stop_price"])
            ),
            execution_reason=request.get("execution_reason"),
            entry_valid_until=(
                None
                if request.get("entry_valid_until") is None
                else datetime.fromisoformat(request["entry_valid_until"])
            ),
            metadata=request.get("metadata", {}),
        ),
        fills=tuple(_fill_from_payload(fill) for fill in value["fills"]),
    )


def _fill_payload(fill: Fill) -> dict:
    return {"fill_id": fill.fill_id, "order_id": fill.order_id, "quantity": str(fill.quantity), "price": str(fill.price), "timestamp": fill.timestamp.isoformat(), "commission": str(fill.commission), "slippage": str(fill.slippage), "venue": fill.venue, "liquidity_flag": fill.liquidity_flag}


def _fill_from_payload(value: dict) -> Fill:
    return Fill(fill_id=value["fill_id"], order_id=value["order_id"], quantity=Decimal(value["quantity"]), price=Decimal(value["price"]), timestamp=datetime.fromisoformat(value["timestamp"]), commission=Decimal(value["commission"]), slippage=Decimal(value["slippage"]), venue=value["venue"], liquidity_flag=value["liquidity_flag"])


def _event_payload(event: PaperRuntimeEvent, campaign_id: str) -> dict:
    order = event.order
    return {"paper_campaign_id": campaign_id, "sequence": event.sequence, "event_type": event.event_type, "timestamp": event.timestamp.isoformat(), "message": event.message, "cycle": event.cycle, "symbol": event.symbol, "source": event.source, "order": None if order is None else {"order_id": order.order_id, "symbol": order.symbol, "side": order.side, "quantity": order.quantity, "status": order.status, "updated_at": order.updated_at.isoformat(), "order_type": order.order_type, "limit_price": order.limit_price, "stop_price": order.stop_price, "filled_quantity": order.filled_quantity, "remaining_quantity": order.remaining_quantity, "average_fill_price": order.average_fill_price, "submitted_at": None if order.submitted_at is None else order.submitted_at.isoformat(), "lifecycle_id": order.lifecycle_id, "execution_reason": order.execution_reason, "execution_source": order.execution_source}, "fill": None if event.fill is None else {"request_id": event.fill.request_id, "symbol": event.fill.symbol, "side": event.fill.side, "quantity": str(event.fill.quantity), "fill_price": str(event.fill.fill_price), "notional": str(event.fill.notional), "realized_pnl": str(event.fill.realized_pnl), "timestamp": event.fill.timestamp.isoformat()}}


def _payload_campaign(value: dict) -> str:
    return str(value.get("paper_campaign_id") or LEGACY_PAPER_CAMPAIGN_ID)


def _valid_nonnegative(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _valid_positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _event_from_payload(value: dict) -> PaperRuntimeEvent:
    order = value["order"]
    fill = value["fill"]
    return PaperRuntimeEvent(
        sequence=value["sequence"], timestamp=datetime.fromisoformat(value["timestamp"]), event_type=value.get("event_type", "ORDER_REPLAY"), message=value["message"], cycle=value["cycle"], symbol=value["symbol"], source="paper-execution-replay",
        order=None if order is None else OperationsOrder(order_id=order["order_id"], symbol=order["symbol"], side=order["side"], quantity=order["quantity"], status=order["status"], updated_at=datetime.fromisoformat(order["updated_at"]), order_type=order.get("order_type"), limit_price=order.get("limit_price"), stop_price=order.get("stop_price"), filled_quantity=order.get("filled_quantity"), remaining_quantity=order.get("remaining_quantity"), average_fill_price=order.get("average_fill_price"), submitted_at=None if order.get("submitted_at") is None else datetime.fromisoformat(order["submitted_at"]), lifecycle_id=order.get("lifecycle_id"), execution_reason=order.get("execution_reason"), execution_source=order.get("execution_source")),
        fill=None if fill is None else PaperFill(request_id=fill["request_id"], symbol=fill["symbol"], side=fill["side"], quantity=Decimal(fill["quantity"]), fill_price=Decimal(fill["fill_price"]), notional=Decimal(fill["notional"]), realized_pnl=Decimal(fill["realized_pnl"]), timestamp=datetime.fromisoformat(fill["timestamp"])),
    )


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_SECONDS",
    "DurablePaperExecutionStore",
    "SCHEMA_VERSION",
    "LEGACY_PAPER_CAMPAIGN_ID",
    "NO_ACTIVE_PAPER_CAMPAIGN_ID",
    "DEFAULT_ATLAS_PAPER_STARTING_CASH",
]
