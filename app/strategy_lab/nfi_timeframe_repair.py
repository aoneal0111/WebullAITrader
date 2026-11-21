"""Separate OHLCV aggregation from complete five-minute groups; never fill gaps."""
import json
from hashlib import sha256
from pathlib import Path
import shutil

TIMEFRAMES = {'15m': 15, '1h': 60, '4h': 240, '1d': 1440}
COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume']


def digest(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def aggregate(frame, timeframe):
    import numpy as np
    import pandas as pd
    if timeframe not in TIMEFRAMES or list(frame.columns) != COLUMNS:
        raise ValueError('EXPECTED_OHLCV_AND_SUPPORTED_TIMEFRAME_REQUIRED')
    frame = frame.copy()
    frame['date'] = pd.to_datetime(frame['date'], utc=True)
    if frame.empty or frame['date'].isna().any() or frame['date'].duplicated().any():
        raise ValueError('UNIQUE_VALID_FIVE_MINUTE_TIMES_REQUIRED')
    if not (frame['date'] == frame['date'].dt.floor('5min')).all():
        raise ValueError('FIVE_MINUTE_OPEN_ALIGNMENT_REQUIRED')
    values = frame[COLUMNS[1:]].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any()
        or (values[:, 4] < 0).any()
        or (frame['high'] < frame[['open', 'close', 'low']].max(axis=1)).any()
        or (frame['low'] > frame[['open', 'close', 'high']].min(axis=1)).any()):
        raise ValueError('VALID_FINITE_SOURCE_OHLCV_REQUIRED')
    frame = frame.sort_values('date').set_index('date')
    rule = str(TIMEFRAMES[timeframe]) + 'min'
    grouped = frame.resample(rule, origin='epoch', closed='left', label='left')
    result = grouped.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    counts = grouped['close'].count()
    result = result.loc[counts == TIMEFRAMES[timeframe] // 5].reset_index()
    # Open timestamps stay at the start of each bin. Native Freqtrade applies
    # informative-candle completion timing when merging; do not shift twice.
    return result[COLUMNS]


def prepare(work, history, pairs, *, startup=800):
    import pandas as pd
    work, history = Path(work), Path(history)
    output = work / 'derived-history'
    if output.exists():
        raise ValueError('FRESH_DERIVED_HISTORY_DIRECTORY_REQUIRED')
    start, end = pd.Timestamp('2024-01-01', tz='UTC'), pd.Timestamp('2024-02-01', tz='UTC')
    originals = sorted((history / 'binance').glob('*.feather'))
    if not originals or len(originals) > 800:
        raise ValueError('BOUNDED_HISTORY_REQUIRED')
    needed = sum(p.stat().st_size for p in originals)
    if shutil.disk_usage(work).free < needed + 256 * 1024 ** 2:
        raise ValueError('DERIVED_HISTORY_NEEDS_SEPARATE_COPY_DISK_SPACE')
    output.mkdir()
    folder = output / 'binance'; folder.mkdir()
    report = {'scope': 'COMPLETE_5M_OHLCV_AGGREGATION_RESEARCH_V1',
        'status': 'PREPARING', 'qualification_eligible': False, 'orders': 0,
        'network_requests': 0, 'source_files_modified': False, 'repairs': [],
        'files': {}, 'coverage_policy': 'REPLACE_ONLY_PERIOD_EMPTY_HIGHER_TIMEFRAMES',
        'limitations': ['Derived higher timeframes are not the original provider candles',
            'Incomplete five-minute groups are omitted; missing prices and volume are never fabricated',
            'Open timestamps retained; native informative merge enforces candle completion timing',
            'Other partial historical gaps are not repaired']}
    try:
        for path in originals:
            if path.is_symlink():
                raise ValueError('REGULAR_HISTORY_FILES_REQUIRED')
            shutil.copyfile(path, folder / path.name)
        for pair in pairs:
            stem = pair.replace('/', '_')
            base_path = history / 'binance' / (stem + '-5m.feather')
            base = pd.read_feather(base_path)[COLUMNS]
            dates = pd.to_datetime(base['date'], utc=True)
            if not ((dates >= start) & (dates < end)).any():
                continue
            for tf in TIMEFRAMES:
                target = folder / (stem + '-' + tf + '.feather')
                original = history / 'binance' / target.name
                times = pd.to_datetime(pd.read_feather(original, columns=['date'])['date'], utc=True)
                if ((times >= start) & (times < end)).any():
                    continue
                result = aggregate(base, tf)
                rd = result['date']
                if (int((rd < start).sum()) < startup
                    or not ((rd >= start) & (rd < end)).any()):
                    raise ValueError('DERIVED_TIMEFRAME_HAS_INSUFFICIENT_WARMUP_OR_PERIOD:' + pair + ':' + tf)
                result.to_feather(target)
                report['repairs'].append({'pair': pair, 'timeframe': tf,
                    'source_5m_sha256': digest(base_path), 'original_sha256': digest(original),
                    'derived_sha256': digest(target), 'rows': len(result),
                    'january_rows': int(((rd >= start) & (rd < end)).sum()),
                    'warmup_rows': int((rd < start).sum())})
        for path in sorted(folder.glob('*.feather')):
            report['files'][path.name] = digest(path)
        report['status'] = 'DERIVED_HISTORY_READY'
        print('DERIVED_TIMEFRAMES=' + str(len(report['repairs'])) + '; ORIGINALS_PRESERVED', flush=True)
    except Exception as exc:
        report.update(status='FAILED', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        (work / 'timeframe-repair.json').write_text(json.dumps(report, indent=2) + '\n')
    return output, report
