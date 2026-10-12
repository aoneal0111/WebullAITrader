"""Bounded read-only projection of a saved futures simulator ledger."""
from dataclasses import dataclass
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal as D, localcontext
import json
from pathlib import Path
import sqlite3

from app.asset_modules.contract_serialization import decode
from app.asset_modules.engine_catalog import EngineId


@dataclass(frozen=True)
class FuturesLedgerView:
    status: str
    path: str
    read_at: str
    notice: str
    cash: str | None = None
    realized_pnl: str | None = None
    reserved_margin: str | None = None
    reserved_risk: str | None = None
    margin_headroom: str | None = None
    positions: tuple = ()
    events: tuple = ()


def _text(value, limit):
    if not isinstance(value,str) or not 0 < len(value) <= limit or not value.isprintable():
        raise ValueError('Invalid saved text')
    return value


def _number(value, *, positive=False, nonnegative=False):
    if not isinstance(value,str) or not 1 <= len(value) <= 80: raise ValueError('Invalid saved number')
    v = D(value)
    if (not v.is_finite() or v.adjusted()>18 or v.as_tuple().exponent < -12
            or positive and v<=0 or nonnegative and v<0):
        raise ValueError('Invalid saved number')
    return v


def _time(value):
    t = datetime.fromisoformat(_text(value,40))
    if t.tzinfo is None or t.utcoffset() is None: raise ValueError('Aware saved time required')
    return t.isoformat()


class FuturesLedgerReader:
    def __init__(self, path=None):
        self.path = None if path is None else Path(path).resolve()

    def select(self, path):
        self.path = Path(path).resolve()

    def read(self):
        at = datetime.now(UTC).isoformat()
        path = '' if self.path is None else str(self.path)
        def unavailable(status,notice): return FuturesLedgerView(status,path,at,notice)
        if self.path is None: return unavailable('UNCONFIGURED','Choose a saved futures paper ledger.')
        try:
            if not self.path.is_file(): return unavailable('NOT_FOUND','No saved futures paper ledger at this path.')
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.05)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                if db.execute('PRAGMA application_id').fetchone()[0]!=0x41544650:
                    return unavailable('INVALID','This file is not a futures paper ledger.')
                account = db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone()
                if account is None: raise ValueError('Missing saved account')
                cash,realized = map(_number,account)
                rows = db.execute('SELECT id,symbol,CASE WHEN length(plan)<=16384 THEN plan ELSE NULL END,entry_fill,reserved_risk,reserved_margin FROM positions ORDER BY id LIMIT 3').fetchall()
                if len(rows)>2: raise ValueError('Saved positions exceed simulator limit')
                positions,margin,risk,exit_fees = [],D(0),D(0),D(0)
                with localcontext() as ctx:
                    ctx.prec = 100
                    for identity,symbol,raw,fill,r,m in rows:
                        if raw is None: raise ValueError('Oversized saved plan')
                        p = decode(raw)
                        if (p.contract.engine is not EngineId.FUTURES or symbol!=p.contract.symbol
                                or p.side not in {'LONG','SHORT'} or type(p.quantity) is not int
                                or not 1 <= p.quantity <= 5): raise ValueError('Invalid saved futures ownership')
                        _text(identity,180); _text(symbol,64)
                        for value in (p.entry,p.stop,p.target,p.contract.multiplier,p.contract.tick,p.contract.margin):
                            _number(str(value),positive=True)
                        fee = _number(str(p.fee_per_side),nonnegative=True)
                        entry = _number(fill,positive=True)
                        rr,mm = _number(r,positive=True),_number(m,positive=True)
                        margin += mm; risk += rr; exit_fees += fee*p.quantity
                        positions.append({'contract':symbol,'side':p.side,'quantity':str(p.quantity),
                            'entry':str(entry),'stop':str(p.stop),'target':str(p.target),
                            'margin':str(mm),'risk':str(rr),'expiry':_time(p.contract.expires_at.isoformat()),
                            'intent':identity})
                    headroom = cash-margin-exit_fees
                events = []
                for when,raw in db.execute('SELECT at,CASE WHEN length(payload)<=16384 THEN payload ELSE NULL END FROM journal ORDER BY id DESC LIMIT 50'):
                    if raw is None: raise ValueError('Oversized saved event')
                    e = json.loads(raw)
                    if (not isinstance(e,dict) or e.get('evidence')!='FUTURES_QUOTE_PROXY_NOT_EXECUTABLE_FILL'
                            or e.get('action') not in {'OPEN','CLOSE'} or e.get('side') not in {'LONG','SHORT'}
                            or type(e.get('quantity')) is not int or not 1<=e['quantity']<=5):
                        raise ValueError('Invalid saved simulator event')
                    events.append({'time':_time(when),'contract':_text(e['symbol'],64),
                        'action':e['action'],'side':e['side'],'quantity':str(e['quantity']),
                        'price':str(_number(e['fill'],positive=True)),
                        'net_pnl':None if e['action']=='OPEN' else str(_number(e['net_pnl'])),
                        'intent':_text(e['intent_id'],180)})
                return FuturesLedgerView('AVAILABLE',path,at,
                    'Saved simulator records. No live quotes, worker status or broker positions are inferred.',
                    str(cash),str(realized),str(margin),str(risk),str(headroom),tuple(positions),tuple(events))
        except sqlite3.OperationalError as error:
            if getattr(error,'sqlite_errorcode',0)&255 in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}:
                return unavailable('BUSY','Ledger is busy. Refresh to retry; inventory is unknown.')
            return unavailable('INVALID','Saved ledger could not be read; inventory is unknown.')
        except (sqlite3.DatabaseError,OSError,ValueError,TypeError,KeyError,ArithmeticError,RecursionError):
            return unavailable('INVALID','Saved ledger could not be read; inventory is unknown.')
