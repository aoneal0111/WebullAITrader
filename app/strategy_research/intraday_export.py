"""Bounded read-only export of saved equity bars/quote paths and crypto bars.

Standard library only. No environment credentials, account/fill tables, trading
clients, provider requests or GUI imports. Missing sources are never created.
"""
import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from zipfile import ZipFile, ZIP_DEFLATED

from app.read_only_sqlite import bounded_rows, read_database

LIMIT = 50000
BYTE_LIMIT = 32*1024*1024
PAGE_SLOTS = 2000
MAX_SEQUENCE_SLOTS = 250000
EQUITY_PAGE_QUERY = (
    'SELECT record_type,symbol,timestamp,payload_json FROM capture_records NOT INDEXED '
    'WHERE sequence>? AND sequence<=? AND record_type IN (?,?) '
    'AND julianday(timestamp)>=julianday(?) AND julianday(timestamp)<=julianday(?) '
    'ORDER BY sequence DESC LIMIT ?'
)


def _equity_rows(db, cutoff, at, info):
    """Bound the rowid ranges visited; avoid sorting all matching history."""
    columns = db.execute('PRAGMA table_info(capture_records)').fetchall()
    if not any(c[1]=='sequence' and c[2].upper()=='INTEGER' and c[5]==1 for c in columns):
        raise ValueError('UNKNOWN_EQUITY_SEQUENCE_SCHEMA')
    last = db.execute('SELECT sequence FROM capture_records ORDER BY sequence DESC LIMIT 1').fetchone()
    first = db.execute('SELECT sequence FROM capture_records ORDER BY sequence ASC LIMIT 1').fetchone()
    info.update(query_strategy='BOUNDED_SEQUENCE_WINDOWS_V2', sequence_slots_scanned=0, scan_limited=False)
    if last is None:
        return []
    high, floor = last[0], first[0]
    rows, size = [], 0
    while high >= floor and info['sequence_slots_scanned'] < MAX_SEQUENCE_SLOTS and len(rows) <= LIMIT:
        width = min(PAGE_SLOTS, MAX_SEQUENCE_SLOTS-info['sequence_slots_scanned'])
        lower = max(floor-1, high-width)
        try:
            page = bounded_rows(db,EQUITY_PAGE_QUERY,
                (lower,high,'MINUTE_BAR','EXECUTION_PRICE_PATH',cutoff.isoformat(),at.isoformat(),LIMIT+1-len(rows)),
                LIMIT+1-len(rows),BYTE_LIMIT)
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT or not rows:
                raise
            # Only completed pages survive. Never retry or extend the read deadline.
            info.update(status='SOURCE_ERROR', error_type='OperationalError',
                        error_stage='EQUITY_RECORD_READ', sqlite_errorcode=sqlite3.SQLITE_INTERRUPT,
                        sqlite_errorname='SQLITE_INTERRUPT', partial_read=True,
                        scan_limited=True, truncated=True)
            return rows
        size += sum(len(str(v).encode()) for row in page for v in row)
        if size > BYTE_LIMIT:
            raise ValueError('DATABASE_RESULT_LIMIT')
        rows.extend(page)
        info['sequence_slots_scanned'] += high-lower
        high = lower
    info['scan_limited'] = high >= floor and len(rows) <= LIMIT
    info['truncated'] = high >= floor or len(rows) > LIMIT
    return rows


def _time(value):
    at = datetime.fromisoformat(str(value))
    if at.utcoffset() is None:
        raise ValueError('Aware saved timestamp required')
    return at


def _number(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError('Exact decimal source value required')
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError('Nonnegative finite source value required')
    return str(number)


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9./-]{1,32}', value):
        raise ValueError('Canonical bounded symbol required')
    return value


def export_sources(directory, *, equity, crypto, at, days=45):
    at = _time(at)
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError('One to ninety saved days required')
    directory = Path(directory)
    archive = directory.with_suffix('.zip')
    if archive.exists():
        raise FileExistsError('Use a new output path')
    directory.mkdir(parents=True, exist_ok=False)
    cutoff = at-timedelta(days=days)
    manifest = {'schema_version': 1, 'export_version': 3, 'execution_authorized': False, 'orders': 0,
        'captured_at': at.isoformat(), 'cutoff': cutoff.isoformat(), 'sources': [], 'files': [],
        'limitations': ['Saved captures can have gaps, duplicate observations and selection bias.',
                       'Quote paths cover observed owned positions, not all candidate opportunities.',
                       'Exported rows retain recorded availability; they are not independently verified fills.',
                       'Equity record timestamps are observations, not independently proven durable availability.',
                       'Futures and option-chain histories are not supplied by these sources.']}
    for kind, source in (('EQUITY', Path(equity)), ('CRYPTO', Path(crypto))):
        info = {'kind': kind, 'status': 'MISSING', 'exported': 0, 'rejected': 0, 'truncated': False,
                'volume_missing': 0}
        manifest['sources'].append(info)
        if not source.is_file():
            continue
        records = []
        stage = 'OPEN_READ_ONLY_SOURCE'
        try:
            with read_database(source) as db:
                db.execute('BEGIN')
                if kind == 'EQUITY':
                    stage = 'EQUITY_SCHEMA'
                    if db.execute('SELECT schema_version FROM capture_metadata WHERE singleton=1').fetchone() != (1,):
                        raise ValueError('Unknown equity capture schema')
                    stage = 'EQUITY_RECORD_READ'
                    rows = _equity_rows(db,cutoff,at,info)
                else:
                    stage = 'CRYPTO_SCHEMA'
                    if (db.execute('PRAGMA application_id').fetchone()[0] != 0x41544342
                            or db.execute("SELECT value FROM capture_metadata WHERE key='schema'").fetchone()
                            != ('CRYPTO_COMPLETED_BAR_CAPTURE_V1',)):
                        raise ValueError('Unknown crypto capture schema')
                    stage = 'CRYPTO_RECORD_READ'
                    rows = bounded_rows(db,
                        'SELECT interval,symbol,available_at,payload FROM completed_bars '
                        'WHERE julianday(available_at)>=julianday(?) AND julianday(available_at)<=julianday(?) '
                        'ORDER BY available_at DESC,identity DESC LIMIT ?',
                        (cutoff.isoformat(),at.isoformat(),LIMIT+1),LIMIT+1,BYTE_LIMIT)
                info['truncated'] = info['truncated'] or len(rows) > LIMIT
                stage = 'SANITIZE_MARKET_RECORDS'
                for record_type, symbol, available, payload in reversed(rows[:LIMIT]):
                    try:
                        p = json.loads(payload); symbol = _symbol(symbol); available = _time(available)
                        r = {'symbol': symbol, 'available_at': available.isoformat(),
                             'availability_basis': ('RECORDED_OBSERVATION_NOT_DURABLE_TIME' if kind=='EQUITY'
                                                    else 'CRYPTO_RECORDED_DURABLE_AVAILABILITY'),
                             'source_payload_sha256': sha256(payload.encode()).hexdigest()}
                        if kind == 'EQUITY' and record_type == 'EXECUTION_PRICE_PATH':
                            if p.get('action') != 'QUOTE':
                                continue
                            r.update(kind='QUOTE', quote_timestamp=_time(p['quote_timestamp']).isoformat(),
                                     bid=_number(p['bid']), ask=_number(p['ask']))
                            identity = str(p.get('paper_campaign_id', ''))+'|'+str(p.get('lifecycle_id', ''))
                            r['path_digest'] = sha256(identity.encode()).hexdigest()
                            for key in ('future_quote', 'stale_quote', 'missing_interval'):
                                if type(p.get(key)) is bool:
                                    r[key] = p[key]
                        else:
                            if kind == 'EQUITY':
                                if p.get('completed') is not True or p.get('interval') != '1m':
                                    raise ValueError('Uncompleted or incompatible saved bar')
                                opened, closed = _time(p['bar_timestamp']), _time(p['completion_timestamp'])
                                interval = 'M1'
                            else:
                                opened, closed = _time(p['opened_at']), _time(p['closed_at'])
                                interval = str(p['interval'])
                                if p['symbol'] != symbol or interval != record_type:
                                    raise ValueError('Saved crypto identity mismatch')
                            if not opened < closed <= available <= at:
                                raise ValueError('Bar availability contradicts completion')
                            spans = {'M1':1, 'M5':5, 'M15':15}
                            if interval not in spans or closed-opened != timedelta(minutes=spans[interval]):
                                raise ValueError('Unsupported or inconsistent recorded interval')
                            r.update(kind='BAR', interval=interval, opened=opened.isoformat(), closed=closed.isoformat(),
                                     **{k:_number(p[k]) for k in ('open','high','low','close')})
                            r['volume'] = None if kind=='CRYPTO' and p.get('volume') is None else _number(p['volume'])
                            o,h,l,c = (Decimal(r[k]) for k in ('open','high','low','close'))
                            if not 0 < l <= min(o,c) <= max(o,c) <= h:
                                raise ValueError('Invalid recorded OHLC')
                            if r['volume'] is None:
                                info['volume_missing'] += 1
                        records.append(r)
                    except (ValueError, KeyError, TypeError, ArithmeticError):
                        info['rejected'] += 1
            if info['status'] != 'SOURCE_ERROR':
                info['status'] = 'EXPORTED'
        except Exception as exc:
            # Avoid propagating arbitrary source text or host paths into output.
            info['status'] = 'SOURCE_ERROR';info['error_type'] = type(exc).__name__
            info['error_stage'] = stage
            code,name = getattr(exc,'sqlite_errorcode',None),getattr(exc,'sqlite_errorname',None)
            if type(code) is int:
                info['sqlite_errorcode'] = code
            if isinstance(name,str) and re.fullmatch(r'SQLITE_[A-Z_]{1,64}',name):
                info['sqlite_errorname'] = name
            if isinstance(exc,ValueError) and str(exc) in {'UNKNOWN_EQUITY_SEQUENCE_SCHEMA','DATABASE_RESULT_LIMIT'}:
                info['validation_reason'] = str(exc)
        if records:
            name = kind.lower()+'-saved-market-data.jsonl'
            data = ''.join(json.dumps(r,sort_keys=True)+'\n' for r in records).encode()
            (directory/name).write_bytes(data)
            info['exported'] = len(records)
            manifest['files'].append({'name':name,'sha256':sha256(data).hexdigest(),'records':len(records)})
    manifest['status'] = 'EXPORTED_FOR_COVERAGE_REVIEW' if manifest['files'] else 'NO_USABLE_SAVED_DATA'
    if any(s['status']=='SOURCE_ERROR' for s in manifest['sources']):
        manifest['status'] = 'PARTIAL_SOURCE_EXPORT' if manifest['files'] else 'SOURCE_EXPORT_FAILED'
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with archive.open('xb') as out, ZipFile(out,'w',ZIP_DEFLATED) as zipped:
        for name in ['manifest.json',*[f['name'] for f in manifest['files']]]:
            zipped.write(directory/name,name)
    return manifest, archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--equity',type=Path,default=Path('data/warrior_momentum_v1_forward/forward_capture.sqlite3'))
    parser.add_argument('--crypto',type=Path,default=Path('crypto-completed-bars.sqlite3'))
    parser.add_argument('--days',type=int,default=45)
    args=parser.parse_args(argv)
    try:
        result,archive=export_sources(args.output,equity=args.equity,crypto=args.crypto,at=datetime.now(UTC),days=args.days)
        print('INTRADAY_ZIP='+str(archive.resolve()))
        print('STATUS='+result['status'])
        return 2 if any(s['status']=='SOURCE_ERROR' for s in result['sources']) else 0
    except Exception as exc:
        print('EXPORT_FAILED='+type(exc).__name__)
        return 2


if __name__=='__main__': raise SystemExit(main())
