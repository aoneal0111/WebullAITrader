from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock

from app.broker_protocol.models import (
    BrokerOrderRequest,
    BrokerOrderType,
    BrokerSide,
    TimeInForce,
    TradingSession,
)
from app.broker_reliability.models import (
    AtlasOrderState,
    DuplicateOrderError,
    InvalidOrderTransitionError,
    JournalCorruptionError,
    JournalEventType,
    JournalHealth,
    JournalHealthStatus,
    OrderJournalEntry,
    OrderJournalEvent,
    TERMINAL_STATES,
    _atlas_id,
    _aware,
)


SCHEMA_VERSION = 1
_TRANSITIONS = {
    AtlasOrderState.PENDING: {
        AtlasOrderState.SUBMITTED,
        AtlasOrderState.PARTIALLY_FILLED,
        AtlasOrderState.FILLED,
        AtlasOrderState.CANCELLED,
        AtlasOrderState.REJECTED,
        AtlasOrderState.EXPIRED,
    },
    AtlasOrderState.SUBMITTED: {
        AtlasOrderState.PARTIALLY_FILLED,
        AtlasOrderState.FILLED,
        AtlasOrderState.CANCELLED,
        AtlasOrderState.REJECTED,
        AtlasOrderState.EXPIRED,
    },
    AtlasOrderState.PARTIALLY_FILLED: {
        AtlasOrderState.PARTIALLY_FILLED,
        AtlasOrderState.FILLED,
        AtlasOrderState.CANCELLED,
        AtlasOrderState.EXPIRED,
    },
}


class PersistentOrderJournal:
    """SQLite event journal and restart-replayable order projection.

    Each mutation commits its event and current projection in one FULL-synchronous
    transaction. Startup verifies storage integrity and creation-event coverage
    before loading the persisted projection and complete transition history.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = str(Path(database_path).resolve())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS journal_metadata(
              key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders(
              atlas_order_id TEXT PRIMARY KEY,
              broker_order_id TEXT,
              request_json TEXT NOT NULL,
              state TEXT NOT NULL,
              filled_quantity TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              parent_atlas_order_id TEXT,
              root_atlas_order_id TEXT NOT NULL,
              transmission_started INTEGER NOT NULL,
              recovered INTEGER NOT NULL,
              FOREIGN KEY(parent_atlas_order_id) REFERENCES orders(atlas_order_id)
            );
            CREATE INDEX IF NOT EXISTS broker_order_identity
              ON orders(broker_order_id) WHERE broker_order_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS order_events(
              sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
              atlas_order_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              timestamp TEXT NOT NULL,
              previous_state TEXT,
              state TEXT NOT NULL,
              broker_order_id TEXT,
              reason TEXT NOT NULL,
              filled_quantity TEXT NOT NULL,
              FOREIGN KEY(atlas_order_id) REFERENCES orders(atlas_order_id)
            );
            """
        )
        row = self._connection.execute(
            "SELECT value FROM journal_metadata WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO journal_metadata VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row[0]) != SCHEMA_VERSION:
            raise JournalCorruptionError("unsupported order journal schema")
        self.verify()

    def close(self) -> None:
        self._connection.close()

    def record_pending(
        self,
        atlas_order_id: str,
        request: BrokerOrderRequest,
        timestamp: datetime,
        *,
        parent_atlas_order_id: str | None = None,
    ) -> OrderJournalEntry:
        _atlas_id(atlas_order_id)
        _aware(timestamp)
        if parent_atlas_order_id is not None:
            parent = self.get(parent_atlas_order_id)
            root_id = parent.root_atlas_order_id or parent.atlas_order_id
        else:
            root_id = atlas_order_id
        duplicate_found = self._row(atlas_order_id) is not None
        with self._transaction():
            # Recheck under the write lock for concurrent submitters.
            if self._row(atlas_order_id) is not None:
                duplicate_found = True
                self._append_duplicate_event(atlas_order_id, timestamp)
            else:
                self._connection.execute(
                    "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        atlas_order_id,
                        None,
                        _request_json(request),
                        AtlasOrderState.PENDING.value,
                        "0",
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        parent_atlas_order_id,
                        root_id,
                        0,
                        0,
                    ),
                )
                self._event(
                    atlas_order_id,
                    JournalEventType.ORDER_RECORDED,
                    timestamp,
                    None,
                    AtlasOrderState.PENDING,
                    None,
                    "outbound order durably recorded before transmission",
                    Decimal("0"),
                )
        if duplicate_found:
            raise DuplicateOrderError(f"duplicate Atlas order ID rejected: {atlas_order_id}")
        return self.get(atlas_order_id)

    def mark_transmission_started(
        self, atlas_order_id: str, timestamp: datetime, reason: str = "broker transmission started"
    ) -> OrderJournalEntry:
        current = self.get(atlas_order_id)
        if current.transmission_started:
            raise DuplicateOrderError(f"order transmission already attempted: {atlas_order_id}")
        with self._transaction():
            self._connection.execute(
                "UPDATE orders SET transmission_started=1,updated_at=? WHERE atlas_order_id=?",
                (timestamp.isoformat(), atlas_order_id),
            )
            self._event(
                atlas_order_id,
                JournalEventType.TRANSMISSION_STARTED,
                timestamp,
                current.state,
                current.state,
                current.broker_order_id,
                reason,
                current.filled_quantity,
            )
        return self.get(atlas_order_id)

    def transition(
        self,
        atlas_order_id: str,
        state: AtlasOrderState,
        timestamp: datetime,
        *,
        broker_order_id: str | None = None,
        filled_quantity: Decimal | None = None,
        reason: str = "state transition",
        recovered: bool = False,
        event_type: JournalEventType = JournalEventType.STATE_CHANGED,
    ) -> OrderJournalEntry:
        _aware(timestamp)
        current = self.get(atlas_order_id)
        quantity = current.filled_quantity if filled_quantity is None else Decimal(filled_quantity)
        broker_id = broker_order_id or current.broker_order_id
        if state != current.state and state not in _TRANSITIONS.get(current.state, set()):
            raise InvalidOrderTransitionError(
                f"invalid order transition: {current.state.value} -> {state.value}"
            )
        if quantity < current.filled_quantity or quantity > current.request.quantity:
            raise InvalidOrderTransitionError("filled quantity cannot regress or exceed quantity")
        if state is AtlasOrderState.FILLED and quantity != current.request.quantity:
            raise InvalidOrderTransitionError("filled state requires complete quantity")
        if state is AtlasOrderState.PARTIALLY_FILLED and not (
            Decimal("0") < quantity < current.request.quantity
        ):
            raise InvalidOrderTransitionError("partial fill requires an incomplete positive quantity")
        if current.state in TERMINAL_STATES and state == current.state and quantity == current.filled_quantity:
            return current
        with self._transaction():
            self._connection.execute(
                "UPDATE orders SET broker_order_id=?,state=?,filled_quantity=?,updated_at=?,recovered=? "
                "WHERE atlas_order_id=?",
                (
                    broker_id,
                    state.value,
                    str(quantity),
                    timestamp.isoformat(),
                    int(current.recovered or recovered),
                    atlas_order_id,
                ),
            )
            self._event(
                atlas_order_id,
                event_type,
                timestamp,
                current.state,
                state,
                broker_id,
                reason,
                quantity,
            )
        return self.get(atlas_order_id)

    def record_operation(
        self,
        atlas_order_id: str,
        event_type: JournalEventType,
        timestamp: datetime,
        reason: str,
    ) -> OrderJournalEvent:
        if event_type not in {JournalEventType.CANCEL_REQUESTED, JournalEventType.REPLACE_REQUESTED}:
            raise ValueError("unsupported journal operation")
        current = self.get(atlas_order_id)
        with self._transaction():
            self._event(
                atlas_order_id,
                event_type,
                timestamp,
                current.state,
                current.state,
                current.broker_order_id,
                reason,
                current.filled_quantity,
            )
        return self.events(atlas_order_id)[-1]

    def get(self, atlas_order_id: str) -> OrderJournalEntry:
        row = self._row(atlas_order_id)
        if row is None:
            raise KeyError(f"Atlas order is unknown: {atlas_order_id}")
        return _entry(row)

    def find_by_broker_order_id(self, broker_order_id: str) -> OrderJournalEntry | None:
        rows = self._connection.execute(
            "SELECT * FROM orders WHERE broker_order_id=? ORDER BY created_at DESC,atlas_order_id DESC",
            (broker_order_id,),
        ).fetchall()
        entries = tuple(_entry(row) for row in rows)
        return next(
            (entry for entry in entries if entry.state not in TERMINAL_STATES),
            entries[0] if entries else None,
        )

    def orders(self) -> tuple[OrderJournalEntry, ...]:
        rows = self._connection.execute(
            "SELECT * FROM orders ORDER BY created_at,atlas_order_id"
        ).fetchall()
        return tuple(_entry(row) for row in rows)

    def events(self, atlas_order_id: str | None = None) -> tuple[OrderJournalEvent, ...]:
        if atlas_order_id is None:
            rows = self._connection.execute("SELECT * FROM order_events ORDER BY sequence_number").fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM order_events WHERE atlas_order_id=? ORDER BY sequence_number",
                (atlas_order_id,),
            ).fetchall()
        return tuple(_journal_event(row) for row in rows)

    def outstanding(self) -> tuple[OrderJournalEntry, ...]:
        return tuple(order for order in self.orders() if order.state not in TERMINAL_STATES)

    def recovered(self) -> tuple[OrderJournalEntry, ...]:
        return tuple(order for order in self.orders() if order.recovered)

    def verify(self) -> JournalHealth:
        integrity = self._connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise JournalCorruptionError("SQLite journal integrity check failed")
        orders = self.orders()
        events = self.events()
        first_events = {event.atlas_order_id for event in events if event.event_type is JournalEventType.ORDER_RECORDED}
        if any(order.atlas_order_id not in first_events for order in orders):
            raise JournalCorruptionError("order projection has no creation event")
        status = JournalHealthStatus.EMPTY if not orders else JournalHealthStatus.HEALTHY
        return JournalHealth(status, len(orders), len(events), "journal is durable and replayable")

    def _row(self, atlas_order_id: str):
        return self._connection.execute(
            "SELECT * FROM orders WHERE atlas_order_id=?", (atlas_order_id,)
        ).fetchone()

    def _append_duplicate_event(self, atlas_order_id: str, timestamp: datetime) -> None:
        current = self.get(atlas_order_id)
        self._event(
            atlas_order_id,
            JournalEventType.DUPLICATE_REJECTED,
            timestamp,
            current.state,
            current.state,
            current.broker_order_id,
            "duplicate submission rejected; existing Atlas order retained",
            current.filled_quantity,
        )

    def _event(self, atlas_order_id, event_type, timestamp, previous_state, state,
               broker_order_id, reason, filled_quantity) -> None:
        _aware(timestamp)
        if not reason:
            raise ValueError("journal reason must be nonempty")
        self._connection.execute(
            "INSERT INTO order_events(atlas_order_id,event_type,timestamp,previous_state,state,"
            "broker_order_id,reason,filled_quantity) VALUES(?,?,?,?,?,?,?,?)",
            (
                atlas_order_id,
                event_type.value,
                timestamp.isoformat(),
                None if previous_state is None else previous_state.value,
                state.value,
                broker_order_id,
                reason,
                str(filled_quantity),
            ),
        )

    class _Transaction:
        def __init__(self, owner):
            self.owner = owner

        def __enter__(self):
            self.owner._lock.acquire()
            self.owner._connection.execute("BEGIN IMMEDIATE")

        def __exit__(self, exc_type, exc, traceback):
            try:
                self.owner._connection.execute("ROLLBACK" if exc_type else "COMMIT")
            finally:
                self.owner._lock.release()

    def _transaction(self):
        return self._Transaction(self)


def _request_json(value: BrokerOrderRequest) -> str:
    return json.dumps(
        {
            "client_order_id": value.client_order_id,
            "symbol": value.symbol,
            "side": value.side.value,
            "order_type": value.order_type.value,
            "quantity": str(value.quantity),
            "limit_price": None if value.limit_price is None else str(value.limit_price),
            "stop_price": None if value.stop_price is None else str(value.stop_price),
            "time_in_force": value.time_in_force.value,
            "trading_session": value.trading_session.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _request(payload: str) -> BrokerOrderRequest:
    value = json.loads(payload)
    return BrokerOrderRequest(
        value["client_order_id"],
        value["symbol"],
        BrokerSide(value["side"]),
        BrokerOrderType(value["order_type"]),
        Decimal(value["quantity"]),
        None if value["limit_price"] is None else Decimal(value["limit_price"]),
        None if value["stop_price"] is None else Decimal(value["stop_price"]),
        TimeInForce(value["time_in_force"]),
        TradingSession(value.get("trading_session", TradingSession.AUTO.value)),
    )


def _entry(row) -> OrderJournalEntry:
    return OrderJournalEntry(
        atlas_order_id=row[0],
        broker_order_id=row[1],
        request=_request(row[2]),
        state=AtlasOrderState(row[3]),
        filled_quantity=Decimal(row[4]),
        created_at=datetime.fromisoformat(row[5]),
        updated_at=datetime.fromisoformat(row[6]),
        parent_atlas_order_id=row[7],
        root_atlas_order_id=row[8],
        transmission_started=bool(row[9]),
        recovered=bool(row[10]),
    )


def _journal_event(row) -> OrderJournalEvent:
    return OrderJournalEvent(
        sequence_number=row[0],
        atlas_order_id=row[1],
        event_type=JournalEventType(row[2]),
        timestamp=datetime.fromisoformat(row[3]),
        previous_state=None if row[4] is None else AtlasOrderState(row[4]),
        state=AtlasOrderState(row[5]),
        broker_order_id=row[6],
        reason=row[7],
        filled_quantity=Decimal(row[8]),
    )
