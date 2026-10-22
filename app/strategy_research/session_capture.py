"""Bounded historical minute-bar collection; market-data access only.

Backfilled bars support hypothetical OHLC research, not proof of executable
historical fills or live availability. No GUI, order or activation path is used.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from zipfile import ZipFile, ZIP_DEFLATED

from .candidates import Bar, validate_bars

# Frozen current liquid sample, not a historical stocks-in-play universe.
SYMBOLS = (('SPY', 'US_ETF'), ('QQQ', 'US_ETF'), ('AAPL', 'US_STOCK'),
           ('MSFT', 'US_STOCK'), ('NVDA', 'US_STOCK'), ('TSLA', 'US_STOCK'))
MAX_RESPONSE = 1200


class SessionDataError(ValueError):
    """Fixed local validation text only; provider messages are never exported."""


def session_windows(captured_at, *, count=12, end_date=None):
    from app.market.calendar import NYSE, EASTERN
    if captured_at.utcoffset() is None or type(count) is not int or not 6 <= count <= 20:
        raise SessionDataError('aware timestamp and six to twenty sessions required')
    today = captured_at.astimezone(EASTERN).date()
    end = end_date or today-timedelta(days=1)
    if not isinstance(end, date) or end >= today or (today-end).days > 90:
        raise SessionDataError('end date must precede today and be within ninety days')
    schedule = NYSE.schedule(start_date=end-timedelta(days=60), end_date=end).tail(count)
    if len(schedule) != count:
        raise SessionDataError('insufficient calendar sessions')
    return tuple((r.market_open.to_pydatetime(), r.market_close.to_pydatetime())
                 for _, r in schedule.iterrows())


def _timestamp(value):
    # Explicit supported conventions; never infer local timezone.
    text = str(value)
    if re.fullmatch(r'\d{10}|\d{13}', text):
        return datetime.fromtimestamp(int(text)/(1000 if len(text)==13 else 1), UTC)
    stamp = datetime.fromisoformat(text)
    if stamp.utcoffset() is None:
        raise SessionDataError('aware provider bar time required')
    return stamp


def session_bars(symbol, rows, opened, closed):
    if not isinstance(rows, (list, tuple)) or not 0 < len(rows) <= MAX_RESPONSE:
        raise SessionDataError('empty or oversized response')
    expected = int((closed-opened).total_seconds()//60)
    values = {}
    outside = duplicates = 0
    for row in rows:
        if row.get('symbol', symbol) != symbol:
            raise SessionDataError('response symbol mismatch')
        stamp = _timestamp(row['time'])
        if not opened <= stamp < closed:
            outside += 1
            continue
        if stamp.second or stamp.microsecond:
            raise SessionDataError('unaligned minute timestamp')
        numbers = tuple(Decimal(str(row[k])) for k in ('open','high','low','close','volume'))
        if stamp in values:
            if values[stamp] != numbers:
                raise SessionDataError('conflicting minute duplicate')
            duplicates += 1
        values[stamp] = numbers
    missing = sum(opened+timedelta(minutes=i) not in values for i in range(expected))
    coverage = dict(expected_minutes=expected, received_minutes=len(values),
                    missing_minutes=missing, outside_session_rows=outside, identical_duplicates=duplicates)
    if missing:
        return (), coverage
    bars = tuple(Bar(symbol, stamp, stamp+timedelta(minutes=1), *values[stamp], closed)
                 for stamp in sorted(values))
    validate_bars(bars, 'minute')
    return bars, coverage


def capture_sessions(directory, market_data, response_rows, *, captured_at, count=12,
                     end_date=None, pause=time.sleep, progress=None):
    windows = session_windows(captured_at, count=count, end_date=end_date)
    directory = Path(directory)
    archive = directory.with_suffix('.zip')
    if archive.exists() or directory.exists():
        raise FileExistsError('use a new output path')
    directory.mkdir(parents=True)
    split = windows[len(windows)//2][0]
    manifest = dict(schema_version=1, execution_authorized=False, orders=0,
        source='WEBULL_OPENAPI_MARKET_DATA_HISTORY', interval='M1',
        captured_at=captured_at.isoformat(), split_at=split.isoformat(),
        timestamp_convention='PROVIDER_TIME_AS_BAR_OPEN_REQUIRES_EXTERNAL_VERIFICATION',
        availability_basis='HISTORICAL_BACKFILL_NOT_LIVE_OBSERVATION',
        adjustment='PROVIDER_UNADJUSTED_MINUTE_OHLC',
        selection='FIXED_CURRENT_LIQUID_SAMPLE_NOT_POINT_IN_TIME_MOVERS',
        request_limit=len(SYMBOLS)*len(windows), requests=0, no_retries=True,
        requested_sessions=[dict(opened=o.isoformat(),closed=c.isoformat()) for o,c in windows],
        symbols=[s for s,_ in SYMBOLS], sessions=[], datasets=[], errors=[],
        limitations=['Historical bar time convention requires independent confirmation.',
                     'Backfill retrieval time is not historical live availability.',
                     'No historical executable quotes, queue, partial fills or capacity evidence.',
                     'Fixed liquid symbols do not validate small-cap momentum or other markets.',
                     'Missing sessions remain visible; retained data may be selectively complete.',
                     'Date split is for research; visible dates are not an untouched holdout.',
                     'No strategy performance or readiness is inferred from successful collection.'])
    names = ['manifest.json']
    failures = 0
    aborted = False
    for symbol, category in SYMBOLS:
        accepted = []
        for opened, closed in windows:
            if manifest['requests']:
                pause(1.1)
            manifest['requests'] += 1
            info = dict(symbol=symbol, opened=opened.isoformat(), closed=closed.isoformat())
            manifest['sessions'].append(info)
            try:
                expected = int((closed-opened).total_seconds()//60)
                rows = response_rows(market_data.get_history_bar(symbol, category, 'M1',
                    count=str(expected), real_time_required=False,
                    start_time=int(opened.timestamp()*1000), end_time=int(closed.timestamp()*1000)-1))
                bars, coverage = session_bars(symbol, rows, opened, closed)
                info.update(coverage, status='COMPLETE' if bars else 'INCOMPLETE')
                accepted.extend(bars)
                failures = 0
            except Exception as exc:
                info.update(status='ERROR', error_type=type(exc).__name__)
                if isinstance(exc, SessionDataError):
                    info['validation_reason'] = str(exc)
                manifest['errors'].append(dict(symbol=symbol, opened=opened.isoformat(),
                                               error_type=type(exc).__name__))
                failures += 1
            if progress:
                progress(symbol, opened.date().isoformat(), info['status'], info.get('received_minutes',0))
            if failures >= 3:
                aborted = True
                break  # Failures often share credentials/SDK/permission issues.
        if accepted:
            name = symbol+'-minute.csv'
            with (directory/name).open('x',newline='',encoding='utf-8') as out:
                writer=csv.DictWriter(out,fieldnames=asdict(accepted[0]))
                writer.writeheader();writer.writerows(asdict(b) for b in accepted)
            names.append(name)
            manifest['datasets'].append(dict(symbol=symbol,csv=name,
                csv_sha256=sha256((directory/name).read_bytes()).hexdigest(), bars=len(accepted),
                complete_sessions=sum(b.closed==b.session_end for b in accepted)))
        if aborted:
            break
    manifest['aborted_after_consecutive_errors'] = aborted
    manifest['status'] = ('COMPLETE' if len(manifest['sessions'])==manifest['request_limit']
        and all(s['status']=='COMPLETE' for s in manifest['sessions']) else 'INCOMPLETE')
    manifest['implementation_sha256'] = sha256(b''.join(Path(__file__).with_name(n).read_bytes()
        for n in ('session_capture.py','candidates.py'))).hexdigest()
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with archive.open('xb') as out, ZipFile(out,'w',ZIP_DEFLATED) as z:
        for name in names:
            z.write(directory/name,name)
    return manifest, archive


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sessions',type=int,default=12)
    parser.add_argument('--end-date',type=date.fromisoformat)
    args=parser.parse_args(argv)
    try:
        from app.configuration import load_configuration
        from app.webull.client_factories import MarketDataClientFactory, market_data_configuration
        from app.webull.sdk_market_data import _response_rows
        at=datetime.now(UTC)
        session_windows(at,count=args.sessions,end_date=args.end_date)  # before credentials/client access
        if args.output.exists() or args.output.with_suffix('.zip').exists():
            raise FileExistsError('use a new output path')
        client=MarketDataClientFactory(market_data_configuration(load_configuration())).create(timeout_seconds=10)
        result,archive=capture_sessions(args.output,client.market_data,_response_rows,captured_at=at,
            count=args.sessions,end_date=args.end_date,
            progress=lambda s,d,status,n: print(f'SYMBOL={s} DATE={d} STATUS={status} MINUTES={n}',flush=True))
        print('SESSION_ZIP='+str(archive.resolve()));print('STATUS='+result['status'])
        return 0 if result['status']=='COMPLETE' else 2
    except Exception as exc:
        print('CAPTURE_FAILED='+type(exc).__name__)
        return 2


if __name__=='__main__':raise SystemExit(main())
