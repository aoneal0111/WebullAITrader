"""Isolated spot-crypto snapshot simulator. No broker/exchange order calls."""
from contextlib import contextmanager
from datetime import datetime, UTC
from decimal import Decimal as D, localcontext
import json
from hashlib import sha256
from uuid import uuid4
from pathlib import Path
import sqlite3
from threading import RLock


def number(value):
    value = D(str(value))
    if not value.is_finite():
        raise ValueError('Non-finite monetary value')
    return value


class CryptoEntryRefused(ValueError):
    """Known native refusal before any committed fill."""


class CryptoPaper:
    fee = D('0.001')
    slippage = D('0.001')
    max_position = D('250')
    max_loss = D('25')
    maximum_quote_age_seconds = 90

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self._allocation_token=object()
        self.db = sqlite3.connect(path, check_same_thread=False)
        try:
            with self.transaction():
                tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                marker = self.db.execute('PRAGMA application_id').fetchone()[0]
                if marker not in {0,0x41544350}:
                    raise ValueError('Use a dedicated Crypto PAPER ledger')
                if tables and marker==0:
                    expected = {'account':('id','cash','realized'),
                        'positions':('symbol','quantity','entry','stop','target'),
                        'journal':('id','at','payload'),
                        'decisions':('at','symbol','action','outcome','reason')}
                    if tables!=set(expected) or any(tuple(r[1] for r in self.db.execute('PRAGMA table_info('+name+')'))!=cols for name,cols in expected.items()):
                        raise ValueError('Unrecognized legacy Crypto PAPER schema')
                self.db.execute('CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, cash TEXT NOT NULL, realized TEXT NOT NULL)')
                self.db.execute("INSERT OR IGNORE INTO account VALUES (1, '10000', '0')")
                self.db.execute('CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, quantity TEXT, entry TEXT, stop TEXT, target TEXT)')
                self.db.execute('CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, at TEXT, payload TEXT)')
                self.db.execute('CREATE TABLE IF NOT EXISTS decisions (at TEXT, symbol TEXT, action TEXT, outcome TEXT, reason TEXT)')
                self.db.execute('CREATE TABLE IF NOT EXISTS crypto_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                self.db.execute("INSERT OR IGNORE INTO crypto_metadata VALUES ('ledger_id',?)",(uuid4().hex,))
                self.ledger_id = self.db.execute("SELECT value FROM crypto_metadata WHERE key='ledger_id'").fetchone()[0]
                self.db.execute('CREATE TABLE IF NOT EXISTS crypto_intents (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,symbol TEXT NOT NULL,quantity TEXT NOT NULL,entry_cost TEXT NOT NULL,realized_pnl TEXT NOT NULL,opened_at TEXT NOT NULL,closed_at TEXT)')
                self.db.execute('CREATE TABLE IF NOT EXISTS crypto_position_intents (symbol TEXT PRIMARY KEY,intent_id TEXT UNIQUE NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS crypto_dispatches (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,entry_id TEXT,quantity TEXT NOT NULL,net_pnl TEXT,at TEXT NOT NULL)')
                self.db.execute('PRAGMA application_id=0x41544350')
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        # Claim the write lock before reading balances, across processes too.
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def _quote(self, symbol, quote, now):
        if (not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None):
            raise ValueError('Aware crypto execution time required')
        pair = getattr(quote,'pair',None)
        source_at = getattr(quote,'timestamp',None)
        if (pair is None or getattr(pair,'quote_asset',None) != 'USD'
                or getattr(pair,'canonical_symbol',None) != symbol
                or not isinstance(source_at,datetime) or source_at.tzinfo is None
                or source_at.utcoffset() is None):
            raise ValueError('Matching USD pair and aware quote timestamp required')
        if (not 0 <= (now-source_at).total_seconds() <= self.maximum_quote_age_seconds
                or getattr(quote,'bid',None) is None or getattr(quote,'ask',None) is None):
            raise ValueError('Missing or stale crypto quote')
        bid,ask = number(quote.bid),number(quote.ask)
        if not 0 < bid <= ask:
            raise ValueError('Invalid crypto quote')
        return bid,ask,source_at

    @staticmethod
    def _fingerprint(proposal):
        action = proposal['action']
        terms = {'symbol':proposal['symbol'],'action':action}
        fields = ('notional','stop','target') if action=='BUY' else ('fraction',)
        for field in fields:
            value = number(proposal.get(field,'1') if field=='fraction' else proposal[field])
            if len(value.as_tuple().digits)>32 or value.adjusted()>12 or value.as_tuple().exponent < -12:
                raise ValueError('Bounded dispatch monetary terms required')
            with localcontext() as ctx:
                ctx.prec = 100
                terms[field] = str(value.normalize())
        return sha256(json.dumps(terms,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def intent_evidence(self,identity):
        """Authoritative local entry/partial/closed evidence; never broker state."""
        with self.transaction():
            row = self.db.execute('SELECT fingerprint,symbol,quantity,entry_cost,realized_pnl,opened_at,closed_at FROM crypto_intents WHERE id=?',(identity,)).fetchone()
            if row is None: return None
            linked = self.db.execute('SELECT symbol FROM crypto_position_intents WHERE intent_id=?',(identity,)).fetchone()
            position = None if linked is None else self.db.execute('SELECT quantity FROM positions WHERE symbol=?',(linked[0],)).fetchone()
            if row[6] is None and (position is None or linked[0]!=row[1]):
                return {'state':'INCONSISTENT','fingerprint':row[0]}
            if row[6] is not None and linked is not None:
                return {'state':'INCONSISTENT','fingerprint':row[0]}
            return {'state':'OPEN' if row[6] is None else 'CLOSED','fingerprint':row[0],
                'symbol':row[1],'bought_quantity':number(row[2]),'entry_cost':number(row[3]),
                'remaining_quantity':number(position[0]) if position else D(0),
                'realized_pnl':number(row[4]),'opened_at':datetime.fromisoformat(row[5]),
                'settled_at':None if row[6] is None else datetime.fromisoformat(row[6])}

    def dispatch_evidence(self,identity):
        with self.transaction():
            row = self.db.execute('SELECT fingerprint,entry_id,quantity,net_pnl,at FROM crypto_dispatches WHERE id=?',(identity,)).fetchone()
            return None if row is None else {'fingerprint':row[0],'entry_id':row[1],
                'quantity':number(row[2]),'net_pnl':None if row[3] is None else number(row[3]),
                'filled_at':datetime.fromisoformat(row[4])}

    def bind_allocation(self,controller_id,account_id):
        binding=json.dumps([controller_id,account_id])
        with self.transaction():
            old=self.db.execute("SELECT value FROM crypto_metadata WHERE key='allocation'").fetchone()
            if old and old[0]!=binding: raise ValueError('Crypto allocation binding conflict')
            self.db.execute("INSERT OR IGNORE INTO crypto_metadata VALUES ('allocation',?)",(binding,))
        return self._allocation_token

    def active_entry_id(self,symbol):
        with self.transaction():
            row=self.db.execute('SELECT intent_id FROM crypto_position_intents WHERE symbol=?',(symbol,)).fetchone()
            return None if row is None else row[0]

    def prepare_entry(self,proposal,quote,*,now):
        if proposal.get('action')!='BUY': raise ValueError('Spot BUY proposal required')
        bid,ask,source_at=self._quote(proposal['symbol'],quote,now)
        with localcontext() as ctx:
            ctx.prec=100
            price=ask*(1+self.slippage)
            notional=number(proposal['notional']);stop=number(proposal['stop']);target=number(proposal['target'])
            quantity=(notional/price).quantize(D('0.00000001'))
            cost=price*quantity*(1+self.fee)
            risk=cost-stop*(1-self.slippage)*(1-self.fee)*quantity
        return (bid,ask,source_at,price,stop,target,notional,quantity,cost,risk,self._fingerprint(proposal))

    def snapshot(self):
        with self.lock:
            cash, realized = self.db.execute('SELECT cash, realized FROM account WHERE id=1').fetchone()
            rows = self.db.execute('SELECT * FROM positions ORDER BY symbol').fetchall()
            journal = self.db.execute('SELECT at,payload FROM journal ORDER BY rowid DESC LIMIT 100').fetchall()
        return {'cash': cash, 'realized': realized,
                'positions': [dict(zip(('symbol','quantity','entry','stop','target'), row)) for row in rows],
                'events': [{'at': at, **json.loads(payload)} for at, payload in journal]}

    def record_decision(self, proposal, outcome):
        with self.transaction():
            self.db.execute('INSERT INTO decisions VALUES (?,?,?,?,?)',
                            (datetime.now(UTC).isoformat(), str(proposal.get('symbol',''))[:40],
                             str(proposal.get('action',''))[:20], str(outcome)[:200],
                             str(proposal.get('reason',''))[:500]))
            self.db.execute('DELETE FROM decisions WHERE rowid NOT IN (SELECT rowid FROM decisions ORDER BY rowid DESC LIMIT 1000)')

    def decisions(self):
        with self.lock:
            rows = self.db.execute('SELECT * FROM decisions ORDER BY rowid DESC LIMIT 100').fetchall()
        return [dict(zip(('at','symbol','action','outcome','reason'), row)) for row in rows]

    def apply(self, proposal, quotes, *, now=None, allocation_token=None, expected_entry=None):
        proposal=dict(proposal)
        now = now or datetime.now(UTC)
        symbol = str(proposal.get('symbol', ''))
        action = proposal.get('action')
        identity = str(proposal.get('id', ''))
        if action not in {'BUY', 'SELL', 'HOLD'} or not identity or len(identity) > 200:
            raise CryptoEntryRefused('Invalid paper proposal')
        if action == 'HOLD':
            return False
        fingerprint = self._fingerprint(proposal)
        bid,ask,source_at = self._quote(symbol,quotes.get(symbol),now)
        with self.transaction():
            old = self.db.execute('SELECT fingerprint FROM crypto_dispatches WHERE id=?',(identity,)).fetchone()
            if old:
                if old[0]!=fingerprint: raise CryptoEntryRefused('Crypto dispatch identity reused with different terms')
                return False
            if self.db.execute('SELECT 1 FROM journal WHERE id=?',(identity,)).fetchone():
                raise CryptoEntryRefused('Legacy dispatch identity lacks verified terms')
            entry_id,exit_pnl = None,None
            last = self.db.execute('SELECT at FROM journal ORDER BY rowid DESC LIMIT 1').fetchone()
            if last and now < datetime.fromisoformat(last[0]):
                raise CryptoEntryRefused('Crypto execution time precedes the last recorded fill')
            cash, realized = map(number, self.db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone())
            position = self.db.execute('SELECT quantity,entry,stop,target FROM positions WHERE symbol=?', (symbol,)).fetchone()
            if action == 'BUY':
                if (self.db.execute("SELECT 1 FROM crypto_metadata WHERE key='allocation'").fetchone()
                        and allocation_token is not self._allocation_token):
                    raise CryptoEntryRefused('Allocation router required')
                if position or self.db.execute('SELECT COUNT(*) FROM positions').fetchone()[0] >= 2:
                    raise CryptoEntryRefused('Position already exists or two-position cap reached')
                if realized <= D('-100'):
                    raise CryptoEntryRefused('Paper loss budget exhausted')
                if (ask-bid)/ask > D('0.01'):
                    raise CryptoEntryRefused('Spread exceeds paper entry budget')
                prepared=self.prepare_entry(proposal,quotes.get(symbol),now=now)
                if expected_entry is not None and prepared!=expected_entry:
                    raise CryptoEntryRefused('Entry requote required')
                _,_,_,price,stop,target,notional,quantity,cost,risk,_=prepared
                if not 0 < notional <= self.max_position or quantity <= 0:
                    raise CryptoEntryRefused('Invalid paper size')
                if not 0 < stop < price < target or risk > self.max_loss or cost > cash:
                    raise CryptoEntryRefused('Paper risk or cash limit exceeded')
                if target-price < 2*(price-stop) + price*2*(self.fee+self.slippage):
                    raise CryptoEntryRefused('Insufficient reward after estimated costs')
                cash -= cost
                self.db.execute('INSERT INTO positions VALUES (?,?,?,?,?)', (symbol,str(quantity),str(cost/quantity),str(stop),str(target)))
                entry_id = identity
                self.db.execute('INSERT INTO crypto_intents VALUES (?,?,?,?,?,?,?,NULL)',
                    (identity,fingerprint,symbol,str(quantity),str(cost),'0',now.isoformat()))
                self.db.execute('INSERT INTO crypto_position_intents VALUES (?,?)',(symbol,identity))
            else:
                if not position:
                    raise CryptoEntryRefused('No position to sell')
                opened = self.db.execute("SELECT at FROM journal WHERE json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.action')='BUY' ORDER BY rowid DESC LIMIT 1",(symbol,)).fetchone()
                if opened is None or source_at < datetime.fromisoformat(opened[0]):
                    raise CryptoEntryRefused('Protection quote precedes position entry or entry evidence is missing')
                held, entry = number(position[0]), number(position[1])
                fraction = number(proposal.get('fraction', '1'))
                if not 0 < fraction <= 1:
                    raise CryptoEntryRefused('Invalid exit fraction')
                quantity = held if fraction == 1 else (held*fraction).quantize(D('0.00000001'))
                if quantity <= 0:
                    raise CryptoEntryRefused('Exit quantity rounds to zero')
                price = bid * (1-self.slippage)
                proceeds = quantity*price*(1-self.fee)
                cash += proceeds
                exit_pnl = proceeds-quantity*entry
                realized += exit_pnl
                linked = self.db.execute('SELECT intent_id FROM crypto_position_intents WHERE symbol=?',(symbol,)).fetchone()
                if linked:
                    entry_id = linked[0]
                    old_pnl = number(self.db.execute('SELECT realized_pnl FROM crypto_intents WHERE id=?',(entry_id,)).fetchone()[0])
                    self.db.execute('UPDATE crypto_intents SET realized_pnl=?,closed_at=? WHERE id=?',
                        (str(old_pnl+exit_pnl),now.isoformat() if quantity==held else None,entry_id))
                if quantity == held:
                    self.db.execute('DELETE FROM positions WHERE symbol=?', (symbol,))
                    self.db.execute('DELETE FROM crypto_position_intents WHERE symbol=?',(symbol,))
                else:
                    self.db.execute('UPDATE positions SET quantity=? WHERE symbol=?', (str(held-quantity), symbol))
            self.db.execute('INSERT INTO crypto_dispatches VALUES (?,?,?,?,?,?)',
                (identity,fingerprint,entry_id,str(quantity),None if exit_pnl is None else str(exit_pnl),now.isoformat()))
            event = {'symbol':symbol, 'action':action, 'quantity':str(quantity), 'fill':str(price),
                     'model':'SNAPSHOT_SIMULATION', 'reason':str(proposal.get('reason',''))[:500]}
            self.db.execute('UPDATE account SET cash=?,realized=? WHERE id=1', (str(cash),str(realized)))
            self.db.execute('INSERT INTO journal VALUES (?,?,?)', (identity,now.isoformat(),json.dumps(event)))
        return True

    def protect(self, quotes, now=None):
        now = now or datetime.now(UTC)
        problems = []
        for position in self.snapshot()['positions']:
            symbol = position['symbol']
            quote = quotes.get(symbol)
            if quote is None or getattr(quote,'bid',None) is None:
                problems.append(f'{symbol}: awaiting fresh protection quote')
                continue
            try:
                bid,_,_ = self._quote(symbol,quote,now)
                if bid <= number(position['stop']) or bid >= number(position['target']):
                    self.apply({'id':f"protection:{symbol}:{quote.timestamp.isoformat()}",
                                'symbol':symbol,'action':'SELL','reason':'Paper stop/target'}, quotes, now=now)
            except (ValueError, KeyError, ArithmeticError):
                problems.append(f'{symbol}: protection quote rejected')
        return problems

    def close(self):
        with self.lock:
            self.db.close()
