"""Isolated SPY/QQQ long-option quote simulator; never calls a broker or AI."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock

from app.asset_modules.contract_logic import Contract, Plan, Quote, exit_reason
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.contract_serialization import number, encode, decode


class OptionsPaper:
    """Durable snapshot fills with explicit cash, fees, slippage and risk caps.

    Configurations must match on reopen. Entries need explicit local activation;
    protection remains available when entries are off. Missing quotes do not fill.
    """
    def __init__(self, path, *, starting_cash=D('2500'), risk_per_trade=D('25'),
                 total_open_risk=D('50'), slippage=D('0.01'), max_contracts=5):
        for value in (starting_cash, risk_per_trade, total_open_risk):
            number(value, positive=True)
        number(slippage)
        if type(max_contracts) is not int or not 1 <= max_contracts <= 5:
            raise ValueError('One to five contracts per trade required')
        self.risk_per_trade, self.total_open_risk = risk_per_trade, total_open_risk
        self.slippage, self.max_contracts = slippage, max_contracts
        self.enabled, self.lock = False, RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
        try:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            application_id = self.db.execute('PRAGMA application_id').fetchone()[0]
            if tables and application_id != 1096044368:
                raise ValueError('Use a dedicated Options PAPER ledger')
            self.db.execute('CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY, settings TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, cash TEXT, realized TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS positions (id TEXT PRIMARY KEY, underlying TEXT UNIQUE, plan TEXT, entry_fill TEXT, opened_at TEXT, last_quote_at TEXT, reserved_risk TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, at TEXT, payload TEXT)')
            settings = json.dumps([str(starting_cash), str(risk_per_trade), str(total_open_risk), str(slippage), max_contracts])
            with self.transaction():
                self.db.execute('PRAGMA application_id=1096044368')
                old = self.db.execute('SELECT settings FROM metadata WHERE id=1').fetchone()
                if old and old[0] != settings:
                    raise ValueError('Options PAPER settings conflict with existing ledger')
                self.db.execute('INSERT OR IGNORE INTO metadata VALUES (1,?)', (settings,))
                self.db.execute('INSERT OR IGNORE INTO account VALUES (1,?,?)', (str(starting_cash), '0'))
        except Exception:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Explicit entry activation required')
        with self.lock:
            self.enabled = enabled

    def _event(self, at, **payload):
        self.db.execute('INSERT INTO journal (at,payload) VALUES (?,?)',
            (at.isoformat(), json.dumps({'evidence': 'OPTION_QUOTE_PROXY_NOT_EXECUTABLE_FILL', **payload}, default=str)))

    def enter(self, identity, p, quote, *, now):
        if not isinstance(identity, str) or not 1 <= len(identity) <= 180:
            raise ValueError('Bounded intent identity required')
        if not isinstance(p, Plan) or not isinstance(quote, Quote):
            raise ValueError('Typed native option plan and quote required')
        c = p.contract
        if (not isinstance(c, Contract) or c.engine is not EngineId.OPTIONS or c.underlying not in {'SPY', 'QQQ'}
                or c.multiplier != D(100) or p.side != 'LONG'
                or type(p.quantity) is not int or not 1 <= p.quantity <= self.max_contracts):
            raise ValueError('Standard SPY/QQQ long options with integer contracts required')
        for value in (p.entry, p.stop, p.target):
            number(value, positive=True)
        number(p.fee_per_side)
        if (not p.stop < p.entry < p.target or p.target - p.entry not in {D('0.05'), D('0.10')}
                or any(v % c.tick for v in (p.entry, p.stop, p.target, self.slippage))):
            raise ValueError('Tick-aligned native stop and 0.05/0.10 premium target required')
        if any(not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None
               for t in (now, p.created_at, p.deadline)):
            raise ValueError('Aware plan and execution timestamps required')
        if not timedelta(0) < p.deadline-p.created_at <= timedelta(seconds=60):
            raise ValueError('Option scalp hold must be at most sixty seconds')
        fingerprint = sha256(encode(p).encode()).hexdigest()
        with self.transaction():
            old = self.db.execute('SELECT fingerprint FROM intents WHERE id=?', (identity,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError('Intent identity reused for a different plan')
                return 'DUPLICATE'
            if not self.enabled:
                return 'ENTRIES_DISABLED'
            if not quote.valid(now) or quote.symbol != c.symbol or quote.bid % c.tick or quote.ask % c.tick:
                return 'QUOTE_UNAVAILABLE'
            if not 0 <= (now - p.created_at).total_seconds() <= 2 or not now < p.deadline < c.expires_at:
                return 'PLAN_EXPIRED_OR_NOT_CURRENT'
            if quote.ask != p.entry:
                return 'REQUOTE_REQUIRED'
            if self.db.execute('SELECT 1 FROM positions WHERE underlying=?', (c.underlying,)).fetchone():
                return 'UNDERLYING_ALREADY_OPEN'
            fill = quote.ask + self.slippage
            cost = (fill * c.multiplier + p.fee_per_side) * p.quantity
            risk = ((fill - p.stop + self.slippage) * c.multiplier + 2 * p.fee_per_side) * p.quantity
            reward = ((p.target - self.slippage - fill) * c.multiplier - 2 * p.fee_per_side) * p.quantity
            cash = D(self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()[0])
            reserved = sum((D(row[0]) for row in self.db.execute('SELECT reserved_risk FROM positions')), D(0))
            if risk > self.risk_per_trade or risk + reserved > self.total_open_risk:
                return 'RISK_LIMIT'
            if reward < risk:
                return 'NET_REWARD_INSUFFICIENT'
            exit_fees = sum((decode(row[0]).fee_per_side*decode(row[0]).quantity
                for row in self.db.execute('SELECT plan FROM positions')), D(0))
            if cost + p.fee_per_side*p.quantity + exit_fees > cash:
                return 'CASH_LIMIT'
            self.db.execute('INSERT INTO intents VALUES (?,?)', (identity, fingerprint))
            self.db.execute('INSERT INTO positions VALUES (?,?,?,?,?,?,?)',
                (identity, c.underlying, encode(p), str(fill), now.isoformat(), quote.source_at.isoformat(), str(risk)))
            self.db.execute('UPDATE account SET cash=? WHERE id=1', (str(cash-cost),))
            self._event(now, intent_id=identity, symbol=c.symbol, action='BUY', quantity=p.quantity,
                fill=fill, fees=p.fee_per_side*p.quantity, reserved_risk=risk)
            return 'OPENED'

    def protect(self, quotes, *, now):
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Aware protection timestamp required')
        results = {}
        with self.transaction():
            rows = self.db.execute('SELECT id,plan,entry_fill,opened_at,last_quote_at FROM positions ORDER BY id').fetchall()
            for identity, raw, entry_fill, opened, last_quote in rows:
                p = decode(raw)
                q = quotes.get(p.contract.symbol)
                if (not isinstance(q, Quote) or q.symbol != p.contract.symbol or not q.valid(now)
                        or now < datetime.fromisoformat(opened)
                        or q.source_at < datetime.fromisoformat(opened)
                        or q.source_at < datetime.fromisoformat(last_quote)
                        or q.bid % p.contract.tick or q.ask % p.contract.tick):
                    results[identity] = 'AWAITING_FRESH_QUOTE'
                    continue
                if now >= p.contract.expires_at:
                    results[identity] = 'EXPIRY_REQUIRES_RECONCILIATION'
                    continue  # No imaginary expiry liquidation or settlement.
                self.db.execute('UPDATE positions SET last_quote_at=? WHERE id=?', (q.source_at.isoformat(), identity))
                reason = exit_reason(p, q, now)
                if reason is None:
                    results[identity] = 'HELD'
                    continue
                fill = max(D(0), q.bid-self.slippage)
                proceeds = (fill*p.contract.multiplier-p.fee_per_side)*p.quantity
                pnl = ((fill-D(entry_fill))*p.contract.multiplier-2*p.fee_per_side)*p.quantity
                cash, realized = map(D, self.db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone())
                self.db.execute('UPDATE account SET cash=?,realized=? WHERE id=1', (str(cash+proceeds), str(realized+pnl)))
                self.db.execute('DELETE FROM positions WHERE id=?', (identity,))
                self._event(now, intent_id=identity, symbol=p.contract.symbol, action='SELL', reason=reason,
                    quantity=p.quantity, fill=fill, fees=p.fee_per_side*p.quantity, net_pnl=pnl)
                results[identity] = reason
        return results

    def snapshot(self):
        with self.lock:
            cash, realized = self.db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone()
            positions = self.db.execute('SELECT id,plan,entry_fill,reserved_risk FROM positions ORDER BY id').fetchall()
            events = self.db.execute('SELECT at,payload FROM journal ORDER BY id DESC LIMIT 100').fetchall()
            return {'scope': 'ISOLATED_OPTIONS_PAPER_QUOTE_PROXY', 'entries_enabled': self.enabled,
                'cash': cash, 'realized_pnl': realized,
                'positions': [{'intent_id': i, 'plan': json.loads(p), 'entry_fill': e, 'reserved_risk': r} for i,p,e,r in positions],
                'events': [{'at': at, **json.loads(p)} for at,p in events]}

    def close(self):
        with self.lock:
            self.enabled = False
            self.db.close()
