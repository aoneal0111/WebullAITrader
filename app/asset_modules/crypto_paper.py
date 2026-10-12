"""Isolated spot-crypto snapshot simulator. No broker/exchange order calls."""
from contextlib import contextmanager
from datetime import datetime, UTC
from decimal import Decimal as D
import json
from pathlib import Path
import sqlite3
from threading import RLock


def number(value):
    value = D(str(value))
    if not value.is_finite():
        raise ValueError('Non-finite monetary value')
    return value


class CryptoPaper:
    fee = D('0.001')
    slippage = D('0.001')
    max_position = D('250')
    max_loss = D('25')

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, cash TEXT NOT NULL, realized TEXT NOT NULL)')
        self.db.execute("INSERT OR IGNORE INTO account VALUES (1, '10000', '0')")
        self.db.execute('CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, quantity TEXT, entry TEXT, stop TEXT, target TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, at TEXT, payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS decisions (at TEXT, symbol TEXT, action TEXT, outcome TEXT, reason TEXT)')
        self.db.commit()

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

    @staticmethod
    def _quote(symbol, quote, now):
        if (not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None):
            raise ValueError('Aware crypto execution time required')
        pair = getattr(quote,'pair',None)
        source_at = getattr(quote,'timestamp',None)
        if (pair is None or getattr(pair,'quote_asset',None) != 'USD'
                or getattr(pair,'canonical_symbol',None) != symbol
                or not isinstance(source_at,datetime) or source_at.tzinfo is None
                or source_at.utcoffset() is None):
            raise ValueError('Matching USD pair and aware quote timestamp required')
        if (not 0 <= (now-source_at).total_seconds() <= 90
                or getattr(quote,'bid',None) is None or getattr(quote,'ask',None) is None):
            raise ValueError('Missing or stale crypto quote')
        bid,ask = number(quote.bid),number(quote.ask)
        if not 0 < bid <= ask:
            raise ValueError('Invalid crypto quote')
        return bid,ask,source_at

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

    def apply(self, proposal, quotes, *, now=None):
        now = now or datetime.now(UTC)
        symbol = str(proposal.get('symbol', ''))
        action = proposal.get('action')
        identity = str(proposal.get('id', ''))
        if action not in {'BUY', 'SELL', 'HOLD'} or not identity or len(identity) > 200:
            raise ValueError('Invalid paper proposal')
        if action == 'HOLD':
            return False
        bid,ask,source_at = self._quote(symbol,quotes.get(symbol),now)
        with self.transaction():
            if self.db.execute('SELECT 1 FROM journal WHERE id=?', (identity,)).fetchone():
                return False
            last = self.db.execute('SELECT at FROM journal ORDER BY rowid DESC LIMIT 1').fetchone()
            if last and now < datetime.fromisoformat(last[0]):
                raise ValueError('Crypto execution time precedes the last recorded fill')
            cash, realized = map(number, self.db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone())
            position = self.db.execute('SELECT quantity,entry,stop,target FROM positions WHERE symbol=?', (symbol,)).fetchone()
            if action == 'BUY':
                if position or self.db.execute('SELECT COUNT(*) FROM positions').fetchone()[0] >= 2:
                    raise ValueError('Position already exists or two-position cap reached')
                if realized <= D('-100'):
                    raise ValueError('Paper loss budget exhausted')
                if (ask-bid)/ask > D('0.01'):
                    raise ValueError('Spread exceeds paper entry budget')
                price = ask * (1+self.slippage)
                stop, target = number(proposal['stop']), number(proposal['target'])
                notional = number(proposal['notional'])
                quantity = (notional/price).quantize(D('0.00000001'))
                if not 0 < notional <= self.max_position or quantity <= 0:
                    raise ValueError('Invalid paper size')
                cost = price * quantity * (1+self.fee)
                risk = cost - stop*(1-self.slippage)*(1-self.fee)*quantity
                if not 0 < stop < price < target or risk > self.max_loss or cost > cash:
                    raise ValueError('Paper risk or cash limit exceeded')
                if target-price < 2*(price-stop) + price*2*(self.fee+self.slippage):
                    raise ValueError('Insufficient reward after estimated costs')
                cash -= cost
                self.db.execute('INSERT INTO positions VALUES (?,?,?,?,?)', (symbol,str(quantity),str(cost/quantity),str(stop),str(target)))
            else:
                if not position:
                    raise ValueError('No position to sell')
                opened = self.db.execute("SELECT at FROM journal WHERE json_extract(payload,'$.symbol')=? AND json_extract(payload,'$.action')='BUY' ORDER BY rowid DESC LIMIT 1",(symbol,)).fetchone()
                if opened is None or source_at < datetime.fromisoformat(opened[0]):
                    raise ValueError('Protection quote precedes position entry or entry evidence is missing')
                held, entry = number(position[0]), number(position[1])
                fraction = number(proposal.get('fraction', '1'))
                if not 0 < fraction <= 1:
                    raise ValueError('Invalid exit fraction')
                quantity = held if fraction == 1 else (held*fraction).quantize(D('0.00000001'))
                if quantity <= 0:
                    raise ValueError('Exit quantity rounds to zero')
                price = bid * (1-self.slippage)
                proceeds = quantity*price*(1-self.fee)
                cash += proceeds
                realized += proceeds - quantity*entry
                if quantity == held:
                    self.db.execute('DELETE FROM positions WHERE symbol=?', (symbol,))
                else:
                    self.db.execute('UPDATE positions SET quantity=? WHERE symbol=?', (str(held-quantity), symbol))
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
