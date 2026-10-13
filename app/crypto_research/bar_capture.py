"""Bounded append-only completed-provider-bar capture, separate from trading."""
from dataclasses import dataclass
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal as D, localcontext
from hashlib import sha256
from pathlib import Path
import json
import sqlite3

from app.assets import AssetType
from app.crypto_research.evidence import CryptoBarInterval, CryptoCompletedBar

MARKER = 0x41544342
SCHEMA = 'CRYPTO_COMPLETED_BAR_CAPTURE_V1'


def _time(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Aware capture timestamp required')
    return value.astimezone(UTC)


def _text(value,limit):
    if not isinstance(value,str) or not value or len(value)>limit:
        raise ValueError('Bounded capture text required')
    return value


def _number(value):
    if (not isinstance(value,D) or not value.is_finite() or value<0
            or len(value.as_tuple().digits)>32 or value.adjusted()>12 or value.as_tuple().exponent < -12):
        raise ValueError('Bounded Decimal capture field required')
    with localcontext() as ctx:
        ctx.prec=100
        return str(value.normalize())


def _record(bar):
    if not isinstance(bar,CryptoCompletedBar) or bar.is_complete is not True:
        raise ValueError('Typed completed provider bar required')
    if bar.observed_at<bar.closed_at:raise ValueError('Bar observed before completion')
    record={'symbol':_text(bar.canonical_symbol,64),'provider_symbol':_text(bar.provider_symbol,64),
        'provider':_text(bar.provider,128),'interval':bar.interval.value,
        **{k:_time(getattr(bar,k)).isoformat() for k in ('opened_at','closed_at','observed_at','decision_cutoff')},
        **{k:_number(getattr(bar,k)) for k in ('open','high','low','close')},
        'volume':None if bar.volume is None else _number(bar.volume)}
    return record


def _identity(record):
    fields=('symbol','provider_symbol','provider','interval','opened_at')
    return sha256(json.dumps([record[k] for k in fields],separators=(',',':')).encode()).hexdigest()


def _fingerprint(record):
    fields={k:v for k,v in record.items() if k not in {'observed_at','decision_cutoff'}}
    return sha256(json.dumps(fields,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _validate(db):
    if db.execute('PRAGMA application_id').fetchone()[0]!=MARKER:
        raise ValueError('Dedicated crypto completed-bar capture required')
    metadata=dict(db.execute('SELECT key,value FROM capture_metadata'))
    if metadata.get('schema')!=SCHEMA:raise ValueError('Unsupported crypto bar capture schema')
    return metadata


@dataclass(frozen=True)
class CaptureBatch:
    stored: int
    duplicates: int


class CryptoBarCapture:
    """Lazy per-batch connection; no worker, network or execution authority."""
    def __init__(self,path,*,capacity=100000):
        if type(capacity) is not int or not 1<=capacity<=1000000:
            raise ValueError('Bounded capture capacity required')
        self.path=Path(path)
        self.capacity=capacity

    def append_many(self,bars,*,captured_at):
        if not isinstance(bars,tuple) or len(bars)>64:raise ValueError('At most 64 completed bars per batch')
        captured_at=_time(captured_at)
        prepared=[]
        for bar in bars:
            record=_record(bar)
            if max(bar.closed_at,bar.observed_at,bar.decision_cutoff)>captured_at:
                raise ValueError('Capture precedes evidence availability')
            prepared.append((record,_identity(record),_fingerprint(record)))
        if not prepared:return CaptureBatch(0,0)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=2)) as db:
            marker=db.execute('PRAGMA application_id').fetchone()[0]
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if marker not in {0,MARKER} or marker==0 and tables:
                raise ValueError('Dedicated crypto completed-bar capture required')
            db.execute('BEGIN IMMEDIATE')
            try:
                # Another capture handle may have established the schema while
                # this connection waited for the write lock.
                marker=db.execute('PRAGMA application_id').fetchone()[0]
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if marker not in {0,MARKER} or marker==0 and tables:
                    raise ValueError('Dedicated crypto completed-bar capture required')
                if marker==0:
                    db.execute('CREATE TABLE capture_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                    db.executemany('INSERT INTO capture_metadata VALUES (?,?)',(('schema',SCHEMA),('capacity',str(self.capacity))))
                    db.execute('CREATE TABLE completed_bars (identity TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,symbol TEXT NOT NULL,interval TEXT NOT NULL,closed_at TEXT NOT NULL,available_at TEXT NOT NULL,payload TEXT NOT NULL)')
                    db.execute('CREATE INDEX completed_bar_window ON completed_bars(symbol,interval,closed_at)')
                    db.execute(f'PRAGMA application_id={MARKER}')
                metadata=_validate(db)
                if metadata.get('capacity')!=str(self.capacity):raise ValueError('Capture capacity is immutable')
                count=db.execute('SELECT COUNT(*) FROM completed_bars').fetchone()[0]
                stored=duplicates=0
                for record,identity,fingerprint in prepared:
                    existing=db.execute('SELECT fingerprint FROM completed_bars WHERE identity=?',(identity,)).fetchone()
                    if existing:
                        if existing[0]!=fingerprint:raise ValueError('Provider bar revision requires review')
                        duplicates+=1;continue
                    if count+stored>=self.capacity:raise ValueError('Completed-bar capture capacity reached')
                    payload=json.dumps(record,sort_keys=True,separators=(',',':'))
                    if len(payload.encode())>4096:raise ValueError('Capture payload exceeds bound')
                    db.execute('INSERT INTO completed_bars VALUES (?,?,?,?,?,?,?)',(identity,fingerprint,
                        record['symbol'],record['interval'],record['closed_at'],captured_at.isoformat(),payload))
                    stored+=1
                db.commit()
                return CaptureBatch(stored,duplicates)
            except BaseException:
                db.rollback();raise


def read_bars(path,symbol,*,as_of,interval=CryptoBarInterval.M1,limit=64):
    """Read-only restart/replay window; enforce first durable availability."""
    path=Path(path).resolve();as_of=_time(as_of)
    _text(symbol,64)
    if not isinstance(interval,CryptoBarInterval) or type(limit) is not int or not 1<=limit<=64:
        raise ValueError('Bounded interval and limit required')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        _validate(db)
        rows=db.execute('SELECT identity,fingerprint,payload,available_at FROM completed_bars WHERE symbol=? AND interval=? AND available_at<=? AND closed_at<=? ORDER BY closed_at DESC LIMIT ?',
            (symbol,interval.value,as_of.isoformat(),as_of.isoformat(),limit)).fetchall()
        bars=[]
        for identity,fingerprint,payload,available in reversed(rows):
            if len(payload.encode())>4096:raise ValueError('Capture payload exceeds bound')
            def pairs(values):
                result={}
                for key,value in values:
                    if key in result:raise ValueError('Duplicate captured field')
                    result[key]=value
                return result
            r=json.loads(payload,object_pairs_hook=pairs)
            expected=set('symbol provider_symbol provider interval opened_at closed_at observed_at decision_cutoff open high low close volume'.split())
            if not isinstance(r,dict) or set(r)!=expected:raise ValueError('Captured fields invalid')
            if _identity(r)!=identity or _fingerprint(r)!=fingerprint:raise ValueError('Captured evidence fingerprint mismatch')
            bar=CryptoCompletedBar(AssetType.CRYPTO,r['symbol'],r['provider_symbol'],CryptoBarInterval(r['interval']),
                *(_time(datetime.fromisoformat(r[k])) for k in ('opened_at','closed_at')),
                *(D(r[k]) for k in ('open','high','low','close')),None if r['volume'] is None else D(r['volume']),
                r['provider'],_time(datetime.fromisoformat(r['observed_at'])),_time(datetime.fromisoformat(r['decision_cutoff'])))
            _record(bar)
            durable_at=_time(datetime.fromisoformat(available))
            if (bar.canonical_symbol!=symbol or bar.interval is not interval
                    or max(bar.closed_at,bar.observed_at,bar.decision_cutoff)>durable_at or durable_at>as_of):
                raise ValueError('Captured evidence availability mismatch')
            bars.append(bar)
        return tuple(bars)
