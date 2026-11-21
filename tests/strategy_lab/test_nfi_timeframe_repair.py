import json
from pathlib import Path

import pandas as pd
import pytest

from app.strategy_lab.nfi_timeframe_repair import aggregate, prepare, digest


def candles(start='2023-12-31T23:30:00Z', periods=12):
    return pd.DataFrame({'date': pd.date_range(start, periods=periods, freq='5min'),
        'open': [10.] * periods, 'high': [12.] * periods,
        'low': [9.] * periods, 'close': [11.] * periods, 'volume': [2.] * periods})


def test_complete_groups_have_exact_ohlcv_and_open_times():
    frame = candles()
    frame.loc[2, 'high'] = 13
    frame.loc[1, 'low'] = 8
    result = aggregate(frame, '15m')
    assert len(result) == 4
    assert result.iloc[0].to_dict() == {'date': pd.Timestamp('2023-12-31T23:30Z'),
        'open': 10., 'high': 13., 'low': 8., 'close': 11., 'volume': 6.}


def test_missing_and_partial_groups_are_omitted_never_filled():
    result = aggregate(candles().drop(index=[0, 4, 11]), '15m')
    assert list(result['date']) == [pd.Timestamp('2024-01-01T00:00Z')]


def test_prefix_cannot_include_future_constituents():
    full = candles()
    prefix = aggregate(full.iloc[:5], '15m')
    assert len(prefix) == 1
    pd.testing.assert_frame_equal(prefix, aggregate(full, '15m').iloc[:1])


def test_duplicates_misalignment_and_bad_prices_refused():
    frame = candles()
    with pytest.raises(ValueError, match='UNIQUE'):
        aggregate(pd.concat([frame, frame.iloc[:1]]), '15m')
    frame.loc[0, 'date'] += pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match='ALIGNMENT'):
        aggregate(frame, '15m')
    frame = candles(); frame.loc[0, 'high'] = 1
    with pytest.raises(ValueError, match='OHLCV'):
        aggregate(frame, '15m')


def test_repair_preserves_originals_and_records_full_output_identity(tmp_path):
    pytest.importorskip('pyarrow')
    history = tmp_path / 'history'; folder = history / 'binance'; folder.mkdir(parents=True)
    work = tmp_path / 'work'; work.mkdir()
    base = candles('2023-12-01T00:00Z', periods=10000)
    base.to_feather(folder / 'BTC_USDT-5m.feather')
    for tf in ('15m', '1h', '4h', '1d'):
        data = aggregate(base, tf)
        if tf == '15m':
            data = data.loc[data['date'] < pd.Timestamp('2024-01-01T00:00Z')]
        data.reset_index(drop=True).to_feather(folder / ('BTC_USDT-' + tf + '.feather'))
    before = {p.name: digest(p) for p in folder.iterdir()}
    derived, report = prepare(work, history, ['BTC/USDT'])
    assert report['status'] == 'DERIVED_HISTORY_READY'
    assert len(report['repairs']) == 1
    assert report['repairs'][0]['timeframe'] == '15m'
    assert report['repairs'][0]['warmup_rows'] >= 800
    assert {p.name: digest(p) for p in folder.iterdir()} == before
    assert digest(derived / 'binance/BTC_USDT-15m.feather') != before['BTC_USDT-15m.feather']
    assert json.loads((work / 'timeframe-repair.json').read_text()) == report
    with pytest.raises(ValueError, match='FRESH'):
        prepare(work, history, ['BTC/USDT'])


def test_insufficient_warmup_stops_without_modifying_source(tmp_path):
    pytest.importorskip('pyarrow')
    history = tmp_path / 'history'; folder = history / 'binance'; folder.mkdir(parents=True)
    work = tmp_path / 'work'; work.mkdir()
    base = candles('2024-01-01T00:00Z', 12)
    base.to_feather(folder / 'BTC_USDT-5m.feather')
    empty = base.iloc[:0]
    for tf in ('15m', '1h', '4h', '1d'):
        empty.to_feather(folder / ('BTC_USDT-' + tf + '.feather'))
    with pytest.raises(ValueError, match='WARMUP'):
        prepare(work, history, ['BTC/USDT'])
    assert pd.read_feather(folder / 'BTC_USDT-15m.feather').empty
    assert json.loads((work / 'timeframe-repair.json').read_text())['status'] == 'FAILED'


def test_native_informative_merge_waits_until_derived_candle_closes():
    strategy = pytest.importorskip('freqtrade.strategy')
    base = candles('2024-01-01T00:00Z', 9)
    informative = aggregate(base, '15m')
    merged = strategy.merge_informative_pair(base, informative, '5m', '15m', ffill=True)
    assert merged.loc[0:1, 'close_15m'].isna().all()
    assert merged.loc[2, 'close_15m'] == 11.
