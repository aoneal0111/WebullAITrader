"""Bounded, offline coverage audit; never fills gaps or authorizes a strategy."""
import argparse
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import zipfile
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 500
MAX_ROW_BYTES = 16 * 1024


def audit_archive(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('ARCHIVE_SIZE_LIMIT')
    cells = []
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_BYTES:
            raise ValueError('ARCHIVE_EXPANSION_LIMIT')
        names = [e.filename for e in entries]
        if len(names) != len(set(names)):
            raise ValueError('DUPLICATE_ARCHIVE_MEMBER')
        for entry in entries:
            if entry.is_dir():
                continue
            if not entry.filename.endswith('.jsonl'):
                raise ValueError('EXPECTED_NORMALIZED_JSONL')
            cell = dict(member=entry.filename, rows=0, invalid_rows=0,
                        duplicate_minutes=0, out_of_order_rows=0,
                        unavailable_previous_close_rows=0, excluded_extended_hours_rows=0)
            seen = {}; symbols = set(); dates = set(); feeds = set(); closes = set()
            previous_at = None
            with archive.open(entry) as stream:
                while True:
                    line = stream.readline(MAX_ROW_BYTES + 1)
                    if not line:
                        break
                    if len(line) > MAX_ROW_BYTES:
                        raise ValueError('ROW_SIZE_LIMIT')
                    cell['rows'] += 1
                    try:
                        row = json.loads(line)
                        at = datetime.fromisoformat(row['timestamp'])
                        if at.tzinfo is None or at.utcoffset() is None:
                            raise ValueError('AWARE_TIMESTAMP_REQUIRED')
                        local = at.astimezone(EASTERN)
                        if (row.get('session') in {'PREMARKET','AFTER_HOURS'}
                                and not time(9,30) <= local.time() < time(16)):
                            cell['excluded_extended_hours_rows'] += 1
                            continue
                        if local.second or local.microsecond or not time(9,30) <= local.time() < time(16):
                            raise ValueError('REGULAR_MINUTE_REQUIRED')
                        values = [Decimal(row[k]) for k in ('open','high','low','close','volume')]
                        o,h,l,c,v = values
                        if (any(not x.is_finite() for x in values) or min(o,h,l,c) <= 0
                                or v < 0 or not l <= min(o,c) <= max(o,c) <= h):
                            raise ValueError('INVALID_OHLCV')
                        symbol = row['symbol']
                        if not isinstance(symbol,str) or not symbol or row.get('session') != 'REGULAR':
                            raise ValueError('INVALID_SESSION_IDENTITY')
                        if not isinstance(row.get('feed'),str) or not row['feed']:
                            raise ValueError('FEED_REQUIRED')
                    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
                        cell['invalid_rows'] += 1
                        continue
                    if previous_at is not None and at < previous_at:
                        cell['out_of_order_rows'] += 1
                    previous_at = at
                    symbols.add(symbol); dates.add(local.date()); feeds.add(row['feed'])
                    try:
                        prior = Decimal(row['previous_close'])
                        if not prior.is_finite() or prior <= 0:
                            raise ValueError('PREVIOUS_CLOSE_UNAVAILABLE')
                        closes.add(prior)
                    except (ValueError, TypeError, KeyError, InvalidOperation):
                        cell['unavailable_previous_close_rows'] += 1
                    if at in seen:
                        cell['duplicate_minutes'] += 1
                    else:
                        seen[at] = (local,h)
            identity_ok = len(symbols) == len(dates) == len(feeds) == 1
            prior_ok = len(closes) == 1 and cell['unavailable_previous_close_rows'] == 0
            missing = None; missing_examples = []; observed_ten_percent = None
            full_grid = False
            if identity_ok:
                day = next(iter(dates)); start = datetime.combine(day,time(9,30),EASTERN)
                expected = {start + timedelta(minutes=i) for i in range(390)}
                actual = {local for local,_ in seen.values()}
                missing_times = sorted(expected - actual)
                missing = len(missing_times)
                missing_examples = [x.isoformat() for x in missing_times[:5]]
                full_grid = actual == expected
                if prior_ok:
                    observed_ten_percent = max(h for _,h in seen.values()) >= next(iter(closes)) * Decimal('1.10')
            usable = bool(identity_ok and prior_ok and full_grid and not any(cell[k] for k in
                ('invalid_rows','duplicate_minutes','out_of_order_rows')))
            cell.update(symbol=next(iter(symbols)) if len(symbols)==1 else None,
                date=next(iter(dates)).isoformat() if len(dates)==1 else None,
                feeds=sorted(feeds),unique_regular_minutes=len(seen),missing_minutes=missing,
                missing_minute_examples=missing_examples,identity_consistent=identity_ok,previous_close_consistent=prior_ok,
                full_390_minute_grid=full_grid,observed_ten_percent_watch=observed_ten_percent,
                data_ready_for_full_day_comparison=usable)
            cells.append(cell)
    return dict(scope='OFFLINE_EARLY_MOMENTUM_HISTORY_READINESS_V1',
        archive_sha256=sha256(path.read_bytes()).hexdigest(),symbol_days=len(cells),
        data_ready_symbol_days=sum(c['data_ready_for_full_day_comparison'] for c in cells),
        ten_percent_watch_observed_days=sum(c['observed_ten_percent_watch'] is True for c in cells),
        ready_watch_days=sum(c['data_ready_for_full_day_comparison'] and c['observed_ten_percent_watch'] is True for c in cells),
        network_requests=0,orders=0,execution_authorized=False,promotion='NONE',cells=cells,
        limitations=['390-minute grid only; half-days are conservatively excluded, not called feed defects',
            'Previous-close consistency is checked within each file, not independently verified',
            'A 10-percent observation is descriptive and never selects trades using future highs',
            'Coverage does not verify liquidity, quotes, small-cap classification or a point-in-time universe',
            'Incomplete files are not repaired, interpolated or used to claim profitability'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    args=parser.parse_args()
    print(json.dumps(audit_archive(args.archive),indent=2))


if __name__=='__main__':
    main()
