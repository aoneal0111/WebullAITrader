"""Isolated, margin-aware long/short futures quote simulator; no broker or AI."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from app.asset_modules.contract_logic import Contract, Plan, Quote, exit_reason
from app.asset_modules.contract_serialization import number, encode, decode
from app.asset_modules.engine_catalog import EngineId


class FuturesPaper:
    def __init__(self, path, *, starting_cash=D('10000'), risk_per_trade=D('25'),
                 total_open_risk=D('50'), slippage_ticks=1, max_contracts=5, max_positions=2):
        for value in (starting_cash,risk_per_trade,total_open_risk): number(value,positive=True)
        if (type(slippage_ticks) is not int or not 0 <= slippage_ticks <= 10
                or type(max_contracts) is not int or not 1 <= max_contracts <= 5
                or type(max_positions) is not int or not 1 <= max_positions <= 2):
            raise ValueError('Bounded integer slippage, contracts and positions required')
        self.risk_per_trade,self.total_open_risk = risk_per_trade,total_open_risk
        self.slippage_ticks,self.max_contracts,self.max_positions = slippage_ticks,max_contracts,max_positions
        self.enabled,self.lock = False,RLock()
        self._allocation_token = object()
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path,timeout=5,isolation_level=None,check_same_thread=False)
        try:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables and self.db.execute('PRAGMA application_id').fetchone()[0] != 0x41544650:
                raise ValueError('Use a dedicated Futures PAPER ledger')
            self.db.execute('CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY, settings TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, cash TEXT, realized TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS positions (id TEXT PRIMARY KEY, symbol TEXT UNIQUE, plan TEXT, entry_fill TEXT, opened_at TEXT, last_quote_at TEXT, reserved_risk TEXT, reserved_margin TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, at TEXT, payload TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS settlements (id TEXT PRIMARY KEY, net_pnl TEXT NOT NULL, settled_at TEXT NOT NULL)')
            settings = json.dumps([str(starting_cash),str(risk_per_trade),str(total_open_risk),slippage_ticks,max_contracts,max_positions])
            with self.transaction():
                self.db.execute('PRAGMA application_id=0x41544650')
                old = self.db.execute('SELECT settings FROM metadata WHERE id=1').fetchone()
                if old and old[0] != settings: raise ValueError('Futures PAPER settings conflict with existing ledger')
                self.db.execute('INSERT OR IGNORE INTO metadata VALUES (1,?)',(settings,))
                self.db.execute('INSERT OR IGNORE INTO metadata VALUES (2,?)',(uuid4().hex,))
                self.ledger_id = self.db.execute('SELECT settings FROM metadata WHERE id=2').fetchone()[0]
                self.db.execute('INSERT OR IGNORE INTO account VALUES (1,?,?)',(str(starting_cash),'0'))
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

    def set_enabled(self,enabled):
        if type(enabled) is not bool: raise ValueError('Explicit entry activation required')
        with self.lock: self.enabled = enabled

    def _event(self,at,**payload):
        self.db.execute('INSERT INTO journal (at,payload) VALUES (?,?)',
            (at.isoformat(),json.dumps({'evidence':'FUTURES_QUOTE_PROXY_NOT_EXECUTABLE_FILL',**payload},default=str)))

    def enter(self,identity,p,quote,*,now,allocation_token=None):
        if not isinstance(identity,str) or not 1 <= len(identity) <= 180:
            raise ValueError('Bounded intent identity required')
        if not isinstance(p,Plan) or not isinstance(p.contract,Contract) or not isinstance(quote,Quote):
            raise ValueError('Typed futures plan and quote required')
        c = p.contract
        if (c.engine is not EngineId.FUTURES or p.side not in {'LONG','SHORT'}
                or type(p.quantity) is not int or not 1 <= p.quantity <= self.max_contracts):
            raise ValueError('Owned long/short futures plan with integer lots required')
        for v in (p.entry,p.stop,p.target): number(v,positive=True)
        number(p.fee_per_side)
        sign = D(1) if p.side=='LONG' else D(-1)
        if (sign*(p.entry-p.stop) <= 0 or sign*(p.target-p.entry) <= 0
                or any(v % c.tick for v in (p.entry,p.stop,p.target))):
            raise ValueError('Tick-aligned entry, structural stop and target required')
        if any(not isinstance(t,datetime) or t.tzinfo is None or t.utcoffset() is None for t in (now,p.created_at,p.deadline)):
            raise ValueError('Aware execution timestamps required')
        if not timedelta(0) < p.deadline-p.created_at <= timedelta(hours=1):
            raise ValueError('Declared futures hold must be at most one hour')
        fingerprint = sha256(encode(p).encode()).hexdigest()
        with self.transaction():
            old = self.db.execute('SELECT fingerprint FROM intents WHERE id=?',(identity,)).fetchone()
            if old:
                if old[0] != fingerprint: raise ValueError('Intent identity reused for a different plan')
                return 'DUPLICATE'
            if not self.enabled: return 'ENTRIES_DISABLED'
            if (self.db.execute('SELECT 1 FROM metadata WHERE id=3').fetchone()
                    and allocation_token is not self._allocation_token):
                return 'ALLOCATION_ROUTER_REQUIRED'
            last = self.db.execute('SELECT at FROM journal ORDER BY id DESC LIMIT 1').fetchone()
            if last and now < datetime.fromisoformat(last[0]): return 'OUT_OF_ORDER_EVENT'
            if quote.symbol != c.symbol or not quote.valid(now) or quote.bid % c.tick or quote.ask % c.tick:
                return 'QUOTE_UNAVAILABLE'
            if not 0 <= (now-p.created_at).total_seconds() <= 2 or not now < p.deadline < c.expires_at:
                return 'PLAN_EXPIRED_OR_NOT_CURRENT'
            executable = quote.ask if p.side=='LONG' else quote.bid
            if executable != p.entry: return 'REQUOTE_REQUIRED'
            rows = self.db.execute('SELECT symbol,plan,reserved_risk,reserved_margin FROM positions').fetchall()
            if any(row[0]==c.symbol for row in rows): return 'CONTRACT_ALREADY_OPEN'
            if len(rows) >= self.max_positions: return 'POSITION_LIMIT'
            slip = c.tick*self.slippage_ticks
            fill = executable+sign*slip
            if fill <= 0: return 'NONPOSITIVE_SIMULATED_PRICE'
            risk = (sign*(fill-p.stop)*c.multiplier+slip*c.multiplier+2*p.fee_per_side)*p.quantity
            reward = (sign*(p.target-fill)*c.multiplier-slip*c.multiplier-2*p.fee_per_side)*p.quantity
            reserved = sum((D(row[2]) for row in rows),D(0))
            if risk > self.risk_per_trade or risk+reserved > self.total_open_risk: return 'RISK_LIMIT'
            if reward < risk: return 'NET_REWARD_INSUFFICIENT'
            cash = D(self.db.execute('SELECT cash FROM account WHERE id=1').fetchone()[0])
            margin = c.margin*p.quantity
            other_margin = sum((D(row[3]) for row in rows),D(0))
            exit_fees = sum((decode(row[1]).fee_per_side*decode(row[1]).quantity for row in rows),D(0))
            fees = p.fee_per_side*p.quantity
            if margin+other_margin+exit_fees+2*fees > cash: return 'MARGIN_LIMIT'
            self.db.execute('INSERT INTO intents VALUES (?,?)',(identity,fingerprint))
            self.db.execute('INSERT INTO positions VALUES (?,?,?,?,?,?,?,?)',
                (identity,c.symbol,encode(p),str(fill),now.isoformat(),quote.source_at.isoformat(),str(risk),str(margin)))
            self.db.execute('UPDATE account SET cash=? WHERE id=1',(str(cash-fees),))
            self._event(now,intent_id=identity,symbol=c.symbol,action='OPEN',side=p.side,quantity=p.quantity,
                fill=fill,fees=fees,reserved_risk=risk,reserved_margin=margin)
            return 'OPENED'

    def protect(self,quotes,*,now):
        if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Aware protection timestamp required')
        results = {}
        with self.transaction():
            rows = self.db.execute('SELECT id,plan,entry_fill,opened_at,last_quote_at FROM positions ORDER BY id').fetchall()
            last = self.db.execute('SELECT at FROM journal ORDER BY id DESC LIMIT 1').fetchone()
            if last and now < datetime.fromisoformat(last[0]):
                return {row[0]:'OUT_OF_ORDER_EVENT' for row in rows}
            for identity,raw,entry_fill,opened,last_quote in rows:
                p = decode(raw)
                q = quotes.get(p.contract.symbol)
                if (not isinstance(q,Quote) or q.symbol != p.contract.symbol or not q.valid(now)
                        or now < datetime.fromisoformat(opened) or q.source_at < datetime.fromisoformat(opened)
                        or q.source_at < datetime.fromisoformat(last_quote)
                        or q.bid % p.contract.tick or q.ask % p.contract.tick):
                    results[identity] = 'AWAITING_FRESH_QUOTE'
                    continue
                if now >= p.contract.expires_at:
                    results[identity] = 'EXPIRY_REQUIRES_RECONCILIATION'
                    continue
                self.db.execute('UPDATE positions SET last_quote_at=? WHERE id=?',(q.source_at.isoformat(),identity))
                reason = exit_reason(p,q,now)
                if reason is None:
                    results[identity] = 'HELD'
                    continue
                sign = D(1) if p.side=='LONG' else D(-1)
                executable = q.bid if p.side=='LONG' else q.ask
                fill = executable-sign*p.contract.tick*self.slippage_ticks
                if fill <= 0:
                    results[identity] = 'NONPOSITIVE_SIMULATED_PRICE'
                    continue
                gross = sign*(fill-D(entry_fill))*p.contract.multiplier*p.quantity
                fee = p.fee_per_side*p.quantity
                cash,realized = map(D,self.db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone())
                self.db.execute('UPDATE account SET cash=?,realized=? WHERE id=1',(str(cash+gross-fee),str(realized+gross-2*fee)))
                self.db.execute('DELETE FROM positions WHERE id=?',(identity,))
                self._event(now,intent_id=identity,symbol=p.contract.symbol,action='CLOSE',side=p.side,reason=reason,
                    quantity=p.quantity,fill=fill,fees=fee,net_pnl=gross-2*fee)
                self.db.execute('INSERT INTO settlements VALUES (?,?,?)',(identity,str(gross-2*fee),now.isoformat()))
                results[identity] = reason
        return results

    def snapshot(self):
        with self.transaction():
            cash,realized = self.db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone()
            rows = self.db.execute('SELECT id,plan,entry_fill,reserved_risk,reserved_margin FROM positions ORDER BY id').fetchall()
            margin = sum((D(row[4]) for row in rows),D(0))
            exit_fees = sum((decode(row[1]).fee_per_side*decode(row[1]).quantity for row in rows),D(0))
            events = self.db.execute('SELECT at,payload FROM journal ORDER BY id DESC LIMIT 100').fetchall()
            return {'scope':'ISOLATED_FUTURES_PAPER_QUOTE_PROXY','entries_enabled':self.enabled,
                'cash':cash,'realized_pnl':realized,'reserved_margin':str(margin),
                'margin_headroom':str(D(cash)-margin-exit_fees),'unrealized_pnl_excluded':True,
                'positions':[{'intent_id':i,'plan':json.loads(p),'entry_fill':e,'reserved_risk':r,'reserved_margin':m} for i,p,e,r,m in rows],
                'events':[{'at':at,**json.loads(payload)} for at,payload in events]}

    def intent_state(self, identity):
        """Durable simulator ownership, including closed intents outside journal pages."""
        with self.transaction():
            if not self.db.execute('SELECT 1 FROM intents WHERE id=?', (identity,)).fetchone():
                return None
            return ('OPEN' if self.db.execute('SELECT 1 FROM positions WHERE id=?', (identity,)).fetchone()
                    else 'CLOSED')

    def bind_allocation(self, controller_id, account_id):
        """One durable shared allocation per simulator ledger, across restarts."""
        binding = json.dumps([controller_id,account_id])
        with self.transaction():
            old = self.db.execute('SELECT settings FROM metadata WHERE id=3').fetchone()
            if old and old[0]!=binding: raise ValueError('Futures ledger allocation binding conflict')
            self.db.execute('INSERT OR IGNORE INTO metadata VALUES (3,?)',(binding,))
            return self._allocation_token

    def intent_evidence(self, identity):
        with self.transaction():
            row = self.db.execute('SELECT fingerprint FROM intents WHERE id=?',(identity,)).fetchone()
            if row is None: return None
            position = self.db.execute('SELECT 1 FROM positions WHERE id=?',(identity,)).fetchone()
            settled = self.db.execute('SELECT net_pnl,settled_at FROM settlements WHERE id=?',(identity,)).fetchone()
            return {'fingerprint':row[0],'state':'OPEN' if position else 'CLOSED',
                    'net_pnl':None if settled is None else D(settled[0]),
                    'settled_at':None if settled is None else datetime.fromisoformat(settled[1])}

    def close(self):
        with self.lock:
            self.enabled = False
            self.db.close()
