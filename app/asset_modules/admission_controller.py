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
from uuid import uuid4

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
        self._validate_common()
        with localcontext() as context:
            context.prec = 100
            if self.capital < self.entry_limit * self.quantity * self.multiplier:
                raise ValueError("Full notional capital must be reserved; use the explicit futures margin model")

    def _validate_common(self):
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
        _aware(self.quote_at)
        _aware(self.expires_at)

    def payload(self):
        data = asdict(self)
        for name,value in tuple(data.items()):
            if isinstance(value,Decimal): data[name] = _decimal_text(value)
        for name in ("quantity", "entry_limit", "structural_stop", "multiplier"):
            data[name] = _decimal_text(getattr(self, name))
        data.update(engine=self.engine.value, capital=_decimal_text(self.capital),
                    loss_budget=_decimal_text(self.loss_budget),
                    quote_at=_aware(self.quote_at).isoformat(),
                    expires_at=_aware(self.expires_at).isoformat())
        return json.dumps(data, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class FuturesEntryReservation(EntryReservation):
    """Explicit supplied-margin PAPER model; cannot relax other asset models."""
    margin_per_contract: Decimal
    execution_cost_budget: Decimal
    ledger_id: str
    plan_fingerprint: str

    def __post_init__(self):
        self._validate_common()
        if (self.engine is not EngineId.FUTURES or self.quantity != self.quantity.to_integral_value()
                or not 1 <= self.quantity <= 5):
            raise ValueError('Futures margin requires owned integer contracts')
        _positive(self.margin_per_contract, 'margin_per_contract')
        if self.execution_cost_budget != Decimal(0):
            _positive(self.execution_cost_budget, 'execution_cost_budget')
        elif not isinstance(self.execution_cost_budget, Decimal):
            raise ValueError('Decimal execution costs required')
        for value, length in ((self.ledger_id,32),(self.plan_fingerprint,64)):
            if not isinstance(value,str) or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Durable ledger and plan identities required')
        with localcontext() as context:
            context.prec = 100
            if self.capital < self.margin_per_contract*self.quantity+self.execution_cost_budget:
                raise ValueError('Capital must cover margin and declared execution costs')
            risk = abs(self.entry_limit-self.structural_stop)*self.quantity*self.multiplier
            if self.loss_budget < risk+self.execution_cost_budget:
                raise ValueError('Loss budget must include declared execution costs')

    def payload(self):
        data = json.loads(super().payload())
        data.update(capital_model='DECLARED_FUTURES_MARGIN',
                    margin_per_contract=_decimal_text(self.margin_per_contract),
                    execution_cost_budget=_decimal_text(self.execution_cost_budget))
        return json.dumps(data,sort_keys=True,separators=(',',':'))


@dataclass(frozen=True)
class OptionsEntryReservation(EntryReservation):
    """Paid long premium plus declared costs; never a futures margin shortcut."""
    execution_cost_budget: Decimal
    ledger_id: str
    plan_fingerprint: str

    def __post_init__(self):
        super().__post_init__()
        if (self.engine is not EngineId.OPTIONS or self.side != 'BUY' or self.multiplier != Decimal(100)
                or self.quantity != self.quantity.to_integral_value() or not 1 <= self.quantity <= 5):
            raise ValueError('Options require owned standard long integer contracts')
        if self.execution_cost_budget != Decimal(0):
            _positive(self.execution_cost_budget,'execution_cost_budget')
        elif not isinstance(self.execution_cost_budget,Decimal):
            raise ValueError('Decimal execution costs required')
        for value,length in ((self.ledger_id,32),(self.plan_fingerprint,64)):
            if not isinstance(value,str) or len(value)!=length or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Durable ledger and plan identities required')
        with localcontext() as context:
            context.prec = 100
            if self.capital < self.entry_limit*self.quantity*self.multiplier+self.execution_cost_budget:
                raise ValueError('Capital must cover full option premium and declared execution costs')
            if self.loss_budget < abs(self.entry_limit-self.structural_stop)*self.quantity*self.multiplier+self.execution_cost_budget:
                raise ValueError('Loss budget must cover price risk and declared execution costs')

    def payload(self):
        data = json.loads(super().payload())
        data.update(capital_model='PAID_OPTION_PREMIUM',execution_cost_budget=_decimal_text(self.execution_cost_budget))
        return json.dumps(data,sort_keys=True,separators=(',',':'))


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
            db.execute('CREATE TABLE IF NOT EXISTS controller_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO controller_metadata VALUES ('identity',?)",(uuid4().hex,))
            self.identity = db.execute("SELECT value FROM controller_metadata WHERE key='identity'").fetchone()[0]
            db.execute('CREATE TABLE IF NOT EXISTS controller_futures_bindings (account_id TEXT PRIMARY KEY,ledger_id TEXT UNIQUE NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS controller_options_bindings (account_id TEXT PRIMARY KEY,ledger_id TEXT UNIQUE NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS controller_options_settlements (command_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,net_pnl TEXT NOT NULL,settled_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS controller_futures_settlements (command_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,net_pnl TEXT NOT NULL,settled_at TEXT NOT NULL)')
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
        if type(request) not in {EntryReservation,FuturesEntryReservation,OptionsEntryReservation}:
            raise ValueError('Trusted typed reservation required')
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
            if isinstance(request,FuturesEntryReservation):
                bound = db.execute('SELECT ledger_id FROM controller_futures_bindings WHERE account_id=?',(request.account_id,)).fetchone()
                if not bound or bound['ledger_id'] != request.ledger_id:
                    return Admission('REJECTED','FUTURES_LEDGER_NOT_BOUND')
            if isinstance(request,OptionsEntryReservation):
                bound = db.execute('SELECT ledger_id FROM controller_options_bindings WHERE account_id=?',(request.account_id,)).fetchone()
                if not bound or bound['ledger_id'] != request.ledger_id:
                    return Admission('REJECTED','OPTIONS_LEDGER_NOT_BOUND')
            last_settlement = db.execute('SELECT settled_at FROM controller_futures_settlements WHERE account_id=? UNION ALL SELECT settled_at FROM controller_options_settlements WHERE account_id=? ORDER BY settled_at DESC LIMIT 1',(request.account_id,request.account_id)).fetchone()
            if last_settlement and now < datetime.fromisoformat(last_settlement[0]):
                return Admission('REJECTED','ALLOCATION_EVENT_OUT_OF_ORDER')
            active = db.execute("""SELECT * FROM controller_commands WHERE account_id=?
                AND state IN ('RESERVED','SUBMITTING','ACKNOWLEDGED')""", (request.account_id,)).fetchall()
            if any(row["instrument"] == request.instrument for row in active):
                return Admission("REJECTED", "INSTRUMENT_ALREADY_RESERVED")
            if len(active) >= self.MAX_ACTIVE_COMMANDS:
                return Admission("REJECTED", "CONTROLLER_CAPACITY")
            pnl = _total(Decimal(row[0]) for row in db.execute(
                'SELECT net_pnl FROM controller_futures_settlements WHERE account_id=? UNION ALL SELECT net_pnl FROM controller_options_settlements WHERE account_id=?',(request.account_id,request.account_id)))
            capital_ceiling = _total((Decimal(account['capital']),min(pnl,Decimal(0))))
            if _total([request.capital, *(Decimal(row["capital"]) for row in active)]) > capital_ceiling:
                return Admission("REJECTED", "SHARED_CAPITAL_LIMIT")
            if _total([request.loss_budget, *(Decimal(row["loss_budget"]) for row in active)]) > Decimal(account["loss_budget"]):
                return Admission("REJECTED", "SHARED_LOSS_BUDGET")
            db.execute("""INSERT INTO controller_commands
                (command_id,account_id,engine,lifecycle_id,instrument,payload,capital,loss_budget,state)
                VALUES (?,?,?,?,?,?,?,?, 'RESERVED')""",
                       (request.command_id, request.account_id, request.engine.value, request.lifecycle_id,
                        request.instrument, payload, str(request.capital), str(request.loss_budget)))
            return Admission("RESERVED")

    def bind_futures_ledger(self, account_id, ledger_id):
        _text(account_id,'account_id')
        _text(ledger_id,'ledger_id')
        with self._transaction() as db:
            if not db.execute('SELECT 1 FROM controller_accounts WHERE account_id=?',(account_id,)).fetchone():
                raise ValueError('Configure the shared account allocation explicitly first')
            rows = db.execute('SELECT account_id,ledger_id FROM controller_futures_bindings WHERE account_id=? OR ledger_id=?',(account_id,ledger_id)).fetchall()
            if any(row['account_id']!=account_id or row['ledger_id']!=ledger_id for row in rows):
                raise ValueError('Futures ledger already belongs to another allocation')
            db.execute('INSERT OR IGNORE INTO controller_futures_bindings VALUES (?,?)',(account_id,ledger_id))

    def bind_options_ledger(self, account_id, ledger_id):
        _text(account_id,'account_id')
        _text(ledger_id,'ledger_id')
        with self._transaction() as db:
            if not db.execute('SELECT 1 FROM controller_accounts WHERE account_id=?',(account_id,)).fetchone():
                raise ValueError('Configure the shared account allocation explicitly first')
            rows = db.execute('SELECT account_id,ledger_id FROM controller_options_bindings WHERE account_id=? OR ledger_id=?',(account_id,ledger_id)).fetchall()
            if any(row['account_id']!=account_id or row['ledger_id']!=ledger_id for row in rows):
                raise ValueError('Options ledger already belongs to another allocation')
            db.execute('INSERT OR IGNORE INTO controller_options_bindings VALUES (?,?)',(account_id,ledger_id))

    def command(self, account_id, command_id):
        with self._transaction() as db:
            row = db.execute('SELECT payload,state,broker_order_id FROM controller_commands WHERE account_id=? AND command_id=?',(account_id,command_id)).fetchone()
            return None if row is None else {'request':json.loads(row['payload']),'state':row['state'],'broker_order_id':row['broker_order_id']}

    def refuse_futures_dispatch(self, command_id, *, reference):
        """Trusted simulator adapter only: a completed local refusal, never a timeout."""
        _text(reference,'refusal reference')
        with self._transaction() as db:
            row = self._owned(db,command_id,EngineId.FUTURES)
            if row['state']!='SUBMITTING' or json.loads(row['payload']).get('capital_model')!='DECLARED_FUTURES_MARGIN':
                raise ValueError('One claimed futures margin dispatch required')
            db.execute("UPDATE controller_commands SET state='REJECTED',reconciliation_reference=? WHERE command_id=?",(reference,command_id))

    def settle_futures_flat(self, command_id, *, ledger_id, plan_fingerprint, net_pnl, settled_at, reference):
        """Trusted simulator reconciliation: durable matching closed intent and net P&L."""
        _text(reference,'settlement reference')
        settled_at = _aware(settled_at).isoformat()
        if not isinstance(net_pnl,Decimal) or not net_pnl.is_finite():
            raise ValueError('Finite Decimal settlement required')
        if net_pnl != 0: _positive(net_pnl.copy_abs(),'settlement magnitude')
        with self._transaction() as db:
            row = self._owned(db,command_id,EngineId.FUTURES)
            payload = json.loads(row['payload'])
            if (payload.get('capital_model')!='DECLARED_FUTURES_MARGIN' or payload.get('ledger_id')!=ledger_id
                    or payload.get('plan_fingerprint')!=plan_fingerprint):
                raise ValueError('Settlement identity mismatch')
            old = db.execute('SELECT net_pnl,settled_at FROM controller_futures_settlements WHERE command_id=?',(command_id,)).fetchone()
            if old:
                if Decimal(old[0])!=net_pnl or old[1]!=settled_at: raise ValueError('Settlement cannot be changed')
                return
            if row['state'] not in {'SUBMITTING','ACKNOWLEDGED'}:
                raise ValueError('Submitted futures intent required')
            db.execute('INSERT INTO controller_futures_settlements VALUES (?,?,?,?)',(command_id,row['account_id'],str(net_pnl),settled_at))
            db.execute("UPDATE controller_commands SET state='CLOSED',reconciliation_reference=? WHERE command_id=?",(reference,command_id))

    def refuse_options_dispatch(self, command_id, *, reference):
        """Trusted simulator adapter only: a completed local refusal, never a timeout."""
        _text(reference,'refusal reference')
        with self._transaction() as db:
            row = self._owned(db,command_id,EngineId.OPTIONS)
            if row['state']!='SUBMITTING' or json.loads(row['payload']).get('capital_model')!='PAID_OPTION_PREMIUM':
                raise ValueError('One claimed options premium dispatch required')
            db.execute("UPDATE controller_commands SET state='REJECTED',reconciliation_reference=? WHERE command_id=?",(reference,command_id))

    def settle_options_flat(self, command_id, *, ledger_id, plan_fingerprint, net_pnl, settled_at, reference):
        """Trusted simulator reconciliation: durable matching closed intent and net P&L."""
        _text(reference,'settlement reference')
        settled_at = _aware(settled_at).isoformat()
        if not isinstance(net_pnl,Decimal) or not net_pnl.is_finite():
            raise ValueError('Finite Decimal settlement required')
        if net_pnl != 0: _positive(net_pnl.copy_abs(),'settlement magnitude')
        with self._transaction() as db:
            row = self._owned(db,command_id,EngineId.OPTIONS)
            payload = json.loads(row['payload'])
            if (payload.get('capital_model')!='PAID_OPTION_PREMIUM' or payload.get('ledger_id')!=ledger_id
                    or payload.get('plan_fingerprint')!=plan_fingerprint):
                raise ValueError('Settlement identity mismatch')
            old = db.execute('SELECT net_pnl,settled_at FROM controller_options_settlements WHERE command_id=?',(command_id,)).fetchone()
            if old:
                if Decimal(old[0])!=net_pnl or old[1]!=settled_at: raise ValueError('Settlement cannot be changed')
                return
            if row['state'] not in {'SUBMITTING','ACKNOWLEDGED'}:
                raise ValueError('Submitted options intent required')
            db.execute('INSERT INTO controller_options_settlements VALUES (?,?,?,?)',(command_id,row['account_id'],str(net_pnl),settled_at))
            db.execute("UPDATE controller_commands SET state='CLOSED',reconciliation_reference=? WHERE command_id=?",(reference,command_id))

    def allocation_balance(self, account_id):
        with self._transaction() as db:
            row = db.execute('SELECT capital FROM controller_accounts WHERE account_id=?',(account_id,)).fetchone()
            if row is None: raise ValueError('Account allocation not configured')
            futures_pnl = _total(Decimal(r[0]) for r in db.execute('SELECT net_pnl FROM controller_futures_settlements WHERE account_id=?',(account_id,)))
            options_pnl = _total(Decimal(r[0]) for r in db.execute('SELECT net_pnl FROM controller_options_settlements WHERE account_id=?',(account_id,)))
            pnl = _total((futures_pnl,options_pnl))
            return {'configured_capital':Decimal(row[0]),'futures_realized_pnl':futures_pnl,
                    'options_realized_pnl':options_pnl,'derivatives_realized_pnl':pnl,
                    'capital_ceiling':_total((Decimal(row[0]),min(pnl,Decimal(0))))}

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
            if json.loads(row['payload']).get('capital_model') in {'DECLARED_FUTURES_MARGIN','PAID_OPTION_PREMIUM'}:
                raise ValueError('Derivative allocation release requires verified settlement')
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
