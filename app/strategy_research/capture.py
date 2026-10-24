"""Bounded market-data-only ETF capture and offline daily screening.

No account data, trading client, order service, GUI, or activation path is used.
Run on the workstation so market-data credentials never leave its configuration.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import time
from zipfile import ZipFile, ZIP_DEFLATED

from .candidates import Bar, validate_bars
from .replay import Costs, compare

# Frozen convenience sample, not a point-in-time universe or an optimized list.
SYMBOLS = ('SPY', 'QQQ', 'IWM', 'DIA', 'EFA', 'EEM', 'XLF', 'XLV')
COUNT = 1200


class CaptureDataError(ValueError):
    """Only fixed local validation messages are safe to export."""


def daily_bars(symbol, rows, *, captured_at):
    from app.market.calendar import EASTERN, NYSE
    if captured_at.utcoffset() is None:
        raise CaptureDataError('aware capture timestamp required')
    today = captured_at.astimezone(EASTERN).date()
    if not 0 < len(rows) <= COUNT:
        raise CaptureDataError('empty or oversized provider response')
    dates = {}
    excluded = 0
    for row in rows:
        stamp = datetime.fromisoformat(str(row['time']))
        if stamp.utcoffset() is None:
            raise CaptureDataError('aware daily label required')
        # Treat the provider timestamp's written date as its daily session label,
        # not a trade instant to shift backward across midnight. Record this in
        # the manifest; fail calendar/continuity checks rather than guess dates.
        day = stamp.date()
        if day > today:
            raise CaptureDataError('future daily label')
        if day == today:
            excluded += 1
            continue  # Never consume today's potentially incomplete daily bar.
        if row.get('symbol', symbol) != symbol:
            raise CaptureDataError('response symbol mismatch')
        values = tuple(Decimal(str(row[k])) for k in ('open', 'high', 'low', 'close', 'volume'))
        if day in dates and dates[day] != values:
            raise CaptureDataError('conflicting daily duplicate')
        dates[day] = values
    if len(dates) < 450:
        raise CaptureDataError('at least 450 distinct completed daily bars required')
    schedule = NYSE.schedule(start_date=min(dates), end_date=max(dates))
    if set(dates) != {d.date() for d in schedule.index}:
        raise CaptureDataError('missing trading days or nontrading daily labels')
    bars = tuple(Bar(symbol, row.market_open.to_pydatetime(), row.market_close.to_pydatetime(),
                     *dates[day.date()], row.market_close.to_pydatetime())
                 for day, row in schedule.iterrows())
    validate_bars(bars, 'daily')
    return bars, excluded


def capture(directory, market_data, response_rows, *, costs, split_at, captured_at, pause=time.sleep, progress=None):
    """Injected history-only port makes account/order access unnecessary."""
    if split_at.utcoffset() is None or captured_at.utcoffset() is None:
        raise CaptureDataError('aware split and capture timestamps required')
    directory = Path(directory)
    archive = directory.with_suffix('.zip')
    if archive.exists():
        raise FileExistsError('archive already exists')
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        'schema_version': 1, 'execution_authorized': False, 'orders': 0,
        'source': 'WEBULL_OPENAPI_MARKET_DATA_HISTORY', 'category': 'US_ETF',
        'interval': 'D', 'requested_count_per_symbol': COUNT,
        'captured_at': captured_at.isoformat(), 'split_at': split_at.isoformat(),
        'costs_per_side': asdict(costs), 'max_hold_bars': 10,
        'selection': 'FIXED_CURRENT_ETF_SAMPLE_NOT_POINT_IN_TIME_UNIVERSE',
        'adjustment': 'PROVIDER_FORWARD_ADJUSTED_DAILY_OHLC',
        'date_convention': 'PROVIDER_TIMESTAMP_WRITTEN_DATE_AS_SESSION_LABEL',
        'today_excluded': True, 'symbols': list(SYMBOLS), 'datasets': [], 'errors': [],
        'limitations': ['Costs are modeling assumptions, not measured executions.',
                       'Fixed surviving ETFs create selection/survivorship bias.',
                       'ETF trades overlap; per-symbol results are not portfolio profits.',
                       'Historical adjustment factors can introduce point-in-time differences.',
                       'Visible validation is not a final untouched holdout.',
                       'No intraday, quote/queue/partial-fill, capacity or live validation.'],
    }
    summary = []
    for i, symbol in enumerate(SYMBOLS):
        if i:
            pause(1.1)  # bounded single pass; no automatic retries
        try:
            rows = response_rows(market_data.get_history_bar(
                symbol, 'US_ETF', 'D', count=str(COUNT), real_time_required=False))
            bars, excluded = daily_bars(symbol, rows, captured_at=captured_at)
            # Require usable development after indicator warmup, plus unseen
            # validation dates. Short provider history is not silently accepted.
            before = sum(b.opened < split_at for b in bars)
            if before < 250 or len(bars) - before < 100:
                raise CaptureDataError('insufficient development or validation history')
            report = compare(bars, costs, timeframe='daily', split_at=split_at, max_hold=10)
            csv_path = directory / (symbol + '-daily.csv')
            with csv_path.open('x', newline='', encoding='utf-8') as out:
                writer = csv.DictWriter(out, fieldnames=asdict(bars[0]))
                writer.writeheader()
                writer.writerows(asdict(b) for b in bars)
            digest = sha256(csv_path.read_bytes()).hexdigest()
            report['input_sha256'] = digest
            report_path = directory / (symbol + '-screening.json')
            report_path.write_text(json.dumps(report, default=str, indent=2), encoding='utf-8')
            manifest['datasets'].append({'symbol': symbol, 'csv': csv_path.name,
                'csv_sha256': digest, 'report': report_path.name,
                'report_sha256': sha256(report_path.read_bytes()).hexdigest(),
                'bars': len(bars), 'first_session': bars[0].opened.isoformat(),
                'last_session': bars[-1].opened.isoformat(), 'excluded_today': excluded})
            for item in report['results']:
                normal, stressed = item['validation'], item['validation_double_cost']
                summary.append({'symbol': symbol, 'candidate': item['candidate'],
                    **{k: normal[k] for k in ('closed_positions', 'wins', 'losses', 'win_rate',
                       'expectancy_r', 'profit_factor_r', 'closed_trade_drawdown_r')},
                    'double_cost_expectancy_r': stressed['expectancy_r']})
            if progress:
                progress(symbol, 'COMPLETE', len(bars))
        except Exception as exc:
            # SDK exception text can contain signed request headers. Never save
            # or print provider exception messages, credentials, or raw responses.
            error = {'symbol': symbol, 'error_type': type(exc).__name__}
            if isinstance(exc, CaptureDataError):
                error['validation_reason'] = str(exc)
            manifest['errors'].append(error)
            if progress:
                progress(symbol, 'ERROR', 0)
    manifest['status'] = 'COMPLETE' if not manifest['errors'] else 'INCOMPLETE'
    manifest['implementation_sha256'] = sha256(b''.join(
        Path(__file__).with_name(n).read_bytes()
        for n in ('capture.py', 'candidates.py', 'replay.py'))).hexdigest()
    (directory / 'manifest.json').write_text(json.dumps(manifest, default=str, indent=2), encoding='utf-8')
    (directory / 'summary.json').write_text(json.dumps(summary, default=str, indent=2), encoding='utf-8')
    names = ['manifest.json', 'summary.json']
    for item in manifest['datasets']:
        names.extend((item['csv'], item['report']))
    with archive.open('xb') as destination, ZipFile(destination, 'w', ZIP_DEFLATED) as zipped:
        for name in sorted(names):
            zipped.write(directory / name, name)
    return manifest, archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--split-at', type=datetime.fromisoformat)
    parser.add_argument('--adverse-bps-per-side', type=Decimal, required=True)
    parser.add_argument('--fee-per-share-per-side', type=Decimal, required=True)
    args = parser.parse_args(argv)
    try:
        costs = Costs(args.adverse_bps_per_side, args.fee_per_share_per_side)
        from app.market.calendar import EASTERN
        from app.configuration import load_configuration
        from app.webull.client_factories import MarketDataClientFactory, market_data_configuration
        from app.webull.sdk_market_data import _response_rows
        at = datetime.now(UTC)
        split = args.split_at or (at.astimezone(EASTERN) - timedelta(days=365)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        client = MarketDataClientFactory(market_data_configuration(load_configuration())).create(timeout_seconds=10)
        result, archive = capture(args.output, client.market_data, _response_rows,
            costs=costs, split_at=split, captured_at=at,
            progress=lambda s, status, n: print(f'SYMBOL={s} STATUS={status} BARS={n}', flush=True))
        print('SCREENING_ZIP=' + str(archive.resolve()))
        print('STATUS=' + result['status'])
        print('DATASETS=' + str(len(result['datasets'])) + ' ERRORS=' + str(len(result['errors'])))
        return 0 if result['status'] == 'COMPLETE' else 2
    except Exception as exc:
        print('CAPTURE_FAILED=' + type(exc).__name__)
        print('Check permitted Python dependencies, market-data access and output path. No order was requested.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
