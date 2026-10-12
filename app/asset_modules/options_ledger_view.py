"""Bounded read-only projection of a saved SPY/QQQ options simulator ledger."""
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal as D, localcontext
import json
from pathlib import Path
import sqlite3

from app.asset_modules.contract_serialization import decode
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.futures_ledger_view import _text, _number, _time


@dataclass(frozen=True)
class OptionsLedgerView:
    status: str
    path: str
    read_at: str
    notice: str
    cash: str | None = None
    realized_pnl: str | None = None
    open_premium_cost: str | None = None
    reserved_risk: str | None = None
    exit_fee_reserve: str | None = None
    cash_headroom: str | None = None
    positions: tuple = ()
    events: tuple = ()


class OptionsLedgerReader:
    def __init__(self,path=None):
        self.path = None if path is None else Path(path).resolve()

    def select(self,path):
        self.path = Path(path).resolve()

    def read(self):
        at = datetime.now(UTC).isoformat()
        path = '' if self.path is None else str(self.path)
        def unavailable(status,notice): return OptionsLedgerView(status,path,at,notice)
        if self.path is None: return unavailable('UNCONFIGURED','Choose a saved options paper ledger.')
        try:
            if not self.path.is_file(): return unavailable('NOT_FOUND','No saved options paper ledger at this path.')
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.05)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                if db.execute('PRAGMA application_id').fetchone()[0]!=0x41544f50:
                    return unavailable('INVALID','This file is not an options paper ledger.')
                account = db.execute('SELECT cash,realized FROM account WHERE id=1').fetchone()
                if account is None: raise ValueError('Missing saved account')
                cash,realized = map(_number,account)
                rows = db.execute('SELECT id,underlying,CASE WHEN length(plan)<=16384 THEN plan ELSE NULL END,entry_fill,reserved_risk FROM positions ORDER BY id LIMIT 3').fetchall()
                if len(rows)>2: raise ValueError('Saved positions exceed simulator limit')
                positions,risk,premium,exit_fees,owned = [],D(0),D(0),D(0),set()
                with localcontext() as ctx:
                    ctx.prec = 100
                    for identity,underlying,raw,fill,r in rows:
                        if raw is None: raise ValueError('Oversized saved plan')
                        p = decode(raw); c = p.contract
                        if (c.engine is not EngineId.OPTIONS or underlying!=c.underlying
                                or underlying not in {'SPY','QQQ'} or underlying in owned
                                or c.right not in {'CALL','PUT'} or c.multiplier!=D(100)
                                or p.side!='LONG' or type(p.quantity) is not int or not 1<=p.quantity<=5
                                or not p.stop<p.entry<p.target or p.target-p.entry not in {D('.05'),D('.10')}):
                            raise ValueError('Invalid saved option ownership')
                        owned.add(underlying)
                        _text(identity,180); _text(c.symbol,64)
                        for value in (p.entry,p.stop,p.target,c.tick,c.strike): _number(str(value),positive=True)
                        if any(v%c.tick for v in (p.entry,p.stop,p.target)):
                            raise ValueError('Invalid saved option tick')
                        _time(p.created_at.isoformat()); _time(p.deadline.isoformat())
                        fee = _number(str(p.fee_per_side),nonnegative=True)
                        entry = _number(fill,positive=True); rr = _number(r,positive=True)
                        cost = entry*c.multiplier*p.quantity
                        risk+=rr; premium+=cost; exit_fees+=fee*p.quantity
                        positions.append({'contract':c.symbol,'underlying':underlying,'right':c.right,
                            'side':'LONG','quantity':str(p.quantity),'entry':str(entry),
                            'stop':str(p.stop),'target':str(p.target),'premium_cost':str(cost),
                            'risk':str(rr),'expiry':_time(c.expires_at.isoformat()),'intent':identity})
                    headroom = cash-exit_fees  # Premium was already paid; do not deduct it again.
                events = []
                for when,raw in db.execute('SELECT at,CASE WHEN length(payload)<=16384 THEN payload ELSE NULL END FROM journal ORDER BY id DESC LIMIT 50'):
                    if raw is None: raise ValueError('Oversized saved event')
                    e = json.loads(raw)
                    if (not isinstance(e,dict) or e.get('evidence')!='OPTION_QUOTE_PROXY_NOT_EXECUTABLE_FILL'
                            or e.get('action') not in {'BUY','SELL'} or type(e.get('quantity')) is not int
                            or not 1<=e['quantity']<=5): raise ValueError('Invalid saved simulator event')
                    price = _number(e['fill'],positive=e['action']=='BUY',nonnegative=True)
                    events.append({'time':_time(when),'contract':_text(e['symbol'],64),
                        'action':e['action'],'side':'LONG','quantity':str(e['quantity']),
                        'price':str(price),'net_pnl':None if e['action']=='BUY' else str(_number(e['net_pnl'])),
                        'intent':_text(e['intent_id'],180)})
                return OptionsLedgerView('AVAILABLE',path,at,
                    'Saved simulator records. No live quotes, Greeks, worker status or broker positions are inferred.',
                    str(cash),str(realized),str(premium),str(risk),str(exit_fees),str(headroom),tuple(positions),tuple(events))
        except sqlite3.OperationalError as error:
            if getattr(error,'sqlite_errorcode',0)&255 in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}:
                return unavailable('BUSY','Ledger is busy. Refresh to retry; inventory is unknown.')
            return unavailable('INVALID','Saved ledger could not be read; inventory is unknown.')
        except (sqlite3.DatabaseError,OSError,ValueError,TypeError,KeyError,ArithmeticError,RecursionError):
            return unavailable('INVALID','Saved ledger could not be read; inventory is unknown.')
