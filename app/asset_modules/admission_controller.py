"""Durable PAPER controller foundation. No broker, AI or desktop integration.

Reservations supplement broker risk checks; they do not authorize a real order.
Only trusted execution adapters may claim/acknowledge/reconcile commands.
"""
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from decimal import Decimal, localcontext
import json
from pathlib import Path
import sqlite3

from app.asset_modules.engine_catalog import EngineId, lifecycle_owner


def _text(value, name):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 240 or not value.isprintable()):
        raise ValueError(f"Invalid {name}")
    return value


def _positive(value, name):
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a positive finite Decimal")
    if len(value.as_tuple().digits) > 24 or value.as_tuple().exponent < -8 or value.adjusted() > 12:
        raise ValueError(f"{name} exceeds supported monetary precision")
    return value


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC)


def _decimal_text(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _total(values):
    with localcontext() as context:
        context.prec = 100
        return sum(values, Decimal(0))


@dataclass(frozen=True)
class EntryReservation:
    command_id: str
    account_id: str
    engine: EngineId
    lifecycle_id: str
    instrument: str
    policy_version: str
    side: str
    quantity: Decimal
    entry_limit: Decimal
    structural_stop: Decimal
    multiplier: Decimal
    capital: Decimal
    loss_budget: Decimal
    quote_at: datetime
    expires_at: datetime

    def __post_init__(self):
        for name in ("command_id", "account_id", "lifecycle_id", "instrument", "policy_version"):
            _text(getattr(self, name), name)
        if not isinstance(self.engine, EngineId) or lifecycle_owner(self.lifecycle_id) is not self.engine:
            raise ValueError("Lifecycle must belong to the requesting engine")
        if self.instrument != self.instrument.upper():
            raise ValueError("Canonical uppercase instrument identity required")
        for name in ("quantity", "entry_limit", "structural_stop", "multiplier"):
            _positive(getattr(self, name), name)
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("BUY or SELL required")
        if not (self.structural_stop < self.entry_limit if self.side == "BUY" else self.structural_stop > self.entry_limit):
            raise ValueError("Structural stop must be on the risk side of entry")
        _positive(self.capital, "capital")
        _positive(self.loss_budget, "loss_budget")
        with localcontext() as context:
            context.prec = 100
            if self.loss_budget < abs(self.entry_limit - self.structural_stop) * self.quantity * self.multiplier:
                raise ValueError("Declared loss budget does not cover planned price risk")
            if self.capital < self.entry_limit * self.quantity * self.multiplier:
                raise ValueError("Full notional capital must be reserved; margin integration unavailable")
        _aware(self.quote_at)
        _aware(self.expires_at)

    def payload(self):
        data = asdict(self)
        for name in ("quantity", "entry_limit", "structural_stop", "multiplier"):
            data[name] = _decimal_text(getattr(self, name))
        data.update(engine=self.engine.value, capital=_decimal_text(self.capital),
                    loss_budget=_decimal_text(self.loss_budget),
                    quote_at=_aware(self.quote_at).isoformat(),
                    expires_at=_aware(self.expires_at).isoformat())
        return json.dumps(data, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Admission:
    state: str
    reason: str = ""


class PaperAdmissionController:
    """Serialize allocation and dispatch claims across processes via SQLite.

    Each account has one explicitly configured allocation, not a fabricated
    buying-power balance. An instrument has one active command across engines.
    Ledger history is retained so terminal command IDs cannot be replayed.
    """
    MAX_QUOTE_AGE_SECONDS = 2
    MAX_ENTRY_VALIDITY_SECONDS = 60
    MAX_ACTIVE_COMMANDS = 1000

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS controller_accounts (
                account_id TEXT PRIMARY KEY, capital TEXT NOT NULL, loss_budget TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS controller_commands (
                command_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, engine TEXT NOT NULL,
                lifecycle_id TEXT NOT NULL, instrument TEXT NOT NULL, payload TEXT NOT NULL,
                capital TEXT NOT NULL, loss_budget TEXT NOT NULL, state TEXT NOT NULL,
                broker_order_id TEXT, reconciliation_reference TEXT)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS controller_instrument_owner
                ON controller_commands(account_id, instrument)
                WHERE state IN ('RESERVED', 'SUBMITTING', 'ACKNOWLEDGED')""")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def configure_account(self, account_id, *, capital: Decimal, loss_budget: Decimal):
        _text(account_id, "account_id")
        _positive(capital, "capital")
        _positive(loss_budget, "loss_budget")
        with self._transaction() as db:
            old = db.execute("SELECT * FROM controller_accounts WHERE account_id=?", (account_id,)).fetchone()
            if old and (Decimal(old["capital"]) != capital or Decimal(old["loss_budget"]) != loss_budget):
                raise ValueError("Account allocation already configured; implicit budget changes forbidden")
            db.execute("INSERT OR IGNORE INTO controller_accounts VALUES (?,?,?)",
                       (account_id, str(capital), str(loss_budget)))

    @staticmethod
    def _validity(payload, now):
        age = (now - datetime.fromisoformat(payload["quote_at"])).total_seconds()
        remaining = (datetime.fromisoformat(payload["expires_at"]) - now).total_seconds()
        if not 0 <= age <= PaperAdmissionController.MAX_QUOTE_AGE_SECONDS:
            return "QUOTE_NOT_CURRENT"
        if not 0 < remaining <= PaperAdmissionController.MAX_ENTRY_VALIDITY_SECONDS:
            return "ENTRY_NOT_CURRENT"
        return ""

    def reserve(self, request: EntryReservation, *, now=None):
        now = _aware(now or datetime.now(UTC))
        payload = request.payload()
        with self._transaction() as db:
            old = db.execute("SELECT * FROM controller_commands WHERE command_id=?", (request.command_id,)).fetchone()
            if old:
                if old["payload"] != payload:
                    raise ValueError("Command identity reused with different request")
                return Admission(old["state"], "EXISTING_COMMAND")
            reason = self._validity(json.loads(payload), now)
            if reason:
                return Admission("REJECTED", reason)
            account = db.execute("SELECT * FROM controller_accounts WHERE account_id=?", (request.account_id,)).fetchone()
            if account is None:
                return Admission("REJECTED", "ACCOUNT_NOT_CONFIGURED")
            active = db.execute("""SELECT * FROM controller_commands WHERE account_id=?
                AND state IN ('RESERVED','SUBMITTING','ACKNOWLEDGED')""", (request.account_id,)).fetchall()
            if any(row["instrument"] == request.instrument for row in active):
                return Admission("REJECTED", "INSTRUMENT_ALREADY_RESERVED")
            if len(active) >= self.MAX_ACTIVE_COMMANDS:
                return Admission("REJECTED", "CONTROLLER_CAPACITY")
            if _total([request.capital, *(Decimal(row["capital"]) for row in active)]) > Decimal(account["capital"]):
                return Admission("REJECTED", "SHARED_CAPITAL_LIMIT")
            if _total([request.loss_budget, *(Decimal(row["loss_budget"]) for row in active)]) > Decimal(account["loss_budget"]):
                return Admission("REJECTED", "SHARED_LOSS_BUDGET")
            db.execute("""INSERT INTO controller_commands
                (command_id,account_id,engine,lifecycle_id,instrument,payload,capital,loss_budget,state)
                VALUES (?,?,?,?,?,?,?,?, 'RESERVED')""",
                       (request.command_id, request.account_id, request.engine.value, request.lifecycle_id,
                        request.instrument, payload, str(request.capital), str(request.loss_budget)))
            return Admission("RESERVED")

    @staticmethod
    def _owned(db, command_id, engine):
        if not isinstance(engine, EngineId):
            raise ValueError("Engine identity required")
        row = db.execute("SELECT * FROM controller_commands WHERE command_id=?", (command_id,)).fetchone()
        if row is None or row["engine"] != engine.value:
            raise ValueError("Command unavailable to this engine")
        return row

    def claim_dispatch(self, command_id, engine, *, now=None):
        """Return True once, before a trusted adapter invokes its broker call.

        A crash after this claim is an uncertain submission. Do not auto-retry.
        Broker client-order idempotency and authoritative reconciliation are
        mandatory for later execution integration.
        """
        now = _aware(now or datetime.now(UTC))
        with self._transaction() as db:
            row = self._owned(db, command_id, engine)
            if row["state"] != "RESERVED":
                return False
            state = "ABANDONED" if self._validity(json.loads(row["payload"]), now) else "SUBMITTING"
            db.execute("UPDATE controller_commands SET state=? WHERE command_id=?", (state, command_id))
            return state == "SUBMITTING"

    def acknowledge(self, command_id, engine, broker_order_id):
        _text(broker_order_id, "broker_order_id")
        with self._transaction() as db:
            row = self._owned(db, command_id, engine)
            if row["state"] == "ACKNOWLEDGED" and row["broker_order_id"] == broker_order_id:
                return
            if row["state"] != "SUBMITTING":
                raise ValueError("Acknowledgment requires one claimed submission")
            db.execute("UPDATE controller_commands SET state='ACKNOWLEDGED',broker_order_id=? WHERE command_id=?",
                       (broker_order_id, command_id))

    def abandon_unsubmitted(self, command_id, engine):
        with self._transaction() as db:
            row = self._owned(db, command_id, engine)
            if row["state"] != "RESERVED":
                raise ValueError("Submitted or uncertain commands require reconciliation")
            db.execute("UPDATE controller_commands SET state='ABANDONED' WHERE command_id=?", (command_id,))

    def reconcile_flat(self, command_id, engine, *, orders_terminal, remaining_quantity: Decimal, reference):
        """Trusted reconciler only: no outstanding order AND no lifecycle position.

        Paper/broker adapters must verify this evidence outside the controller.
        Merely cancelling an order, a timeout, or disabling AI is insufficient.
        """
        _text(reference, "reconciliation reference")
        if (orders_terminal is not True or not isinstance(remaining_quantity, Decimal)
                or not remaining_quantity.is_finite() or remaining_quantity != 0):
            raise ValueError("Verified terminal orders and flat lifecycle required")
        with self._transaction() as db:
            row = self._owned(db, command_id, engine)
            if row["state"] == "CLOSED":
                return
            if row["state"] not in {"SUBMITTING", "ACKNOWLEDGED"}:
                raise ValueError("No submitted command to reconcile")
            db.execute("UPDATE controller_commands SET state='CLOSED',reconciliation_reference=? WHERE command_id=?",
                       (reference, command_id))

    def snapshot(self, account_id):
        with self._transaction() as db:
            rows = db.execute("""SELECT capital,loss_budget,state FROM controller_commands
                WHERE account_id=? AND state IN ('RESERVED','SUBMITTING','ACKNOWLEDGED')""", (account_id,)).fetchall()
            return {"active_commands": len(rows),
                    "reserved_capital": _total(Decimal(row["capital"]) for row in rows),
                    "reserved_loss_budget": _total(Decimal(row["loss_budget"]) for row in rows),
                    "uncertain_submissions": sum(row["state"] == "SUBMITTING" for row in rows)}

    def recovery_commands(self, account_id, *, after_command_id="", limit=100):
        """Bounded restart inventory for a trusted reconciler, with ID pagination."""
        _text(account_id, "account_id")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Recovery page limit must be between 1 and 1000")
        if after_command_id:
            _text(after_command_id, "command cursor")
        with self._transaction() as db:
            rows = db.execute("""SELECT command_id,payload,state,broker_order_id
                FROM controller_commands WHERE account_id=? AND command_id>?
                AND state IN ('RESERVED','SUBMITTING','ACKNOWLEDGED')
                ORDER BY command_id LIMIT ?""", (account_id, after_command_id, limit)).fetchall()
            return tuple(dict(request=json.loads(row["payload"]), state=row["state"],
                              broker_order_id=row["broker_order_id"]) for row in rows)
