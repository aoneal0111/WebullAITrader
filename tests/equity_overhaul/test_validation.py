from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from unittest.mock import patch

import pytest

from app.equity_overhaul import validation as v
from app.equity_overhaul.admission import RELEASE_PROMOTIONS
from app.equity_overhaul.replay import replay
from app.equity_overhaul.strategies import ReplacementSignal
from app.strategy_research.candidates import Bar
from app.strategy_research.replay import Costs


def session(day=date(2026, 7, 1), symbol='SPY'):
    opened, closed = v.schedule(day, day+timedelta(days=1))[day]
    return tuple(Bar(symbol, opened+timedelta(minutes=i), opened+timedelta(minutes=i+1),
                     D(100), D('100.2'), D('99.8'), D('100.1'), D(1000), closed)
                 for i in range(int((closed-opened).total_seconds()/60)))


def dataset(root, bars):
    raw = json.dumps([asdict(b) for b in bars], default=str).encode()
    (root/'SPY-normalized.json').write_bytes(raw)
    manifest = {'source': 'SYNTHETIC_TEST_ONLY', 'normalized_inputs': [
        {'file': 'SPY-normalized.json', 'sha256': sha256(raw).hexdigest()}]}
    (root/'manifest.json').write_text(json.dumps(manifest))
    return manifest


def fixed_signal(history, policy, *, at):
    # Trigger only on the second completed minute of each session.
    if at.minute == 32:
        return ReplacementSignal(policy, 'SPY', at, D(99), D(1), 5)


def test_window_does_not_trade_warmup_or_future_and_preserves_original_behavior():
    first, middle, last = session(date(2026, 6, 30)), session(), session(date(2026, 7, 2))
    bars = first+middle+last
    with patch('app.equity_overhaul.replay.signal', side_effect=fixed_signal):
        original = replay(bars, 'ORB5_LONG_V1', Costs(D(0), D(0)))
        subset = replay(bars, 'ORB5_LONG_V1', Costs(D(0), D(0)),
                        evaluate_from=date(2026, 7, 1), evaluate_until=date(2026, 7, 2))
        standalone = replay(middle, 'ORB5_LONG_V1', Costs(D(0), D(0)))
    assert original['closed_trades'] == 3
    assert subset == standalone
    assert len(subset['trades']) == 1
    assert subset['trades'][0]['entry_at'] == middle[2].opened.isoformat()


def test_warmup_is_visible_to_signal_but_cannot_create_pending_entry():
    bars = session(date(2026, 6, 30))+session()
    seen = []
    def observe(history, policy, *, at):
        seen.append((len(history), at))
    with patch('app.equity_overhaul.replay.signal', side_effect=observe):
        r = replay(bars, 'NOISE_BAND_LONG_V1', Costs(D(0), D(0)),
                   evaluate_from=date(2026, 7, 1), evaluate_until=date(2026, 7, 2))
    assert seen and min(n for n, _ in seen) > 390
    assert all(at.date() == date(2026, 7, 1) for _, at in seen)
    assert r['closed_trades'] == 0


@pytest.mark.parametrize('kwargs', [dict(evaluate_from='2026-07-01'),
    dict(evaluate_until=datetime(2026, 7, 1, tzinfo=UTC)),
    dict(evaluate_from=date(2026, 7, 1), evaluate_until=date(2026, 7, 1))])
def test_invalid_window_boundaries_rejected(kwargs):
    with pytest.raises(ValueError): replay(session(), 'ORB5_LONG_V1', Costs(D(0), D(0)), **kwargs)


def test_loader_detects_tampering_and_calendar_fabrication(tmp_path):
    bars = session(); dataset(tmp_path, bars)
    loaded, _ = v.load_dataset(tmp_path)
    assert loaded['SPY'] == bars
    with (tmp_path/'SPY-normalized.json').open('ab') as f: f.write(b' ')
    with pytest.raises(ValueError, match='hash mismatch'): v.load_dataset(tmp_path)
    # Matching hashes cannot make an invented exchange close valid.
    fake = tuple(replace(b, session_end=bars[59].closed) for b in bars[:60])
    dataset(tmp_path, fake)
    with pytest.raises(ValueError, match='exchange calendar'): v.load_dataset(tmp_path)


def test_loader_accepts_real_early_close_and_dst(tmp_path):
    early = session(date(2025, 11, 28)); assert len(early) == 210
    dataset(tmp_path, early); assert v.load_dataset(tmp_path)[0]['SPY'] == early
    winter = session(date(2026, 1, 2)); summer = session()
    assert winter[0].opened.hour == 14 and summer[0].opened.hour == 13
    dataset(tmp_path, winter+summer); v.load_dataset(tmp_path)


def test_path_traversal_symlink_duplicate_and_missing_minute_rejected(tmp_path):
    manifest = dataset(tmp_path, session())
    for name in ('../outside.json', '/outside.json', 'sub/file.json', 'C:\\secret'):
        manifest['normalized_inputs'][0]['file'] = name
        (tmp_path/'manifest.json').write_text(json.dumps(manifest))
        with pytest.raises(ValueError): v.load_dataset(tmp_path)
    manifest = dataset(tmp_path, session())
    manifest['normalized_inputs'] *= 2
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='duplicate'): v.load_dataset(tmp_path)
    bars = session(); dataset(tmp_path, bars[:10]+bars[11:])
    with pytest.raises(ValueError, match='missing minute'): v.load_dataset(tmp_path)


def test_cluster_metrics_include_zero_trade_days_and_are_deterministic():
    def trade(day, value): return dict(entry_at=f'2026-07-{day:02d}T14:00:00+00:00', net_r=str(value))
    trades = [trade(1, 2), trade(1, -1), trade(2, 0)]
    days = ['2026-07-01', '2026-07-02', '2026-07-06']
    a = v.metrics(trades, days); b = v.metrics(trades, days)
    assert a == b and a['complete_dates'] == 3
    assert a['closed_trades'] == 3 and a['wins'] == 1 and a['losses'] == 1 and a['breakeven'] == 1
    assert D(a['expectancy_r']) == D(1)/3 and a['profit_factor_r'] == '2'
    assert a['date_cluster_expectancy_95pct_interval_r'] is not None
    assert v.metrics([], days)['win_rate'] is None
    with pytest.raises(ValueError): v.metrics([trade(3, 1)], days)


def test_every_missing_data_cell_is_retained_and_cannot_pass(tmp_path):
    (tmp_path/'manifest.json').write_text('{"normalized_inputs": []}')
    report = v.evaluate(tmp_path, v.frozen_plan())
    assert len(report['results']) == 3*3*6*2
    assert len(report['aggregates']) == 3*3*2
    assert all(not x['research_screen_pass'] for x in report['aggregates'])
    assert all('INSUFFICIENT_TRADES' in x['blockers'] for x in report['aggregates'])
    assert report['promotion'] == 'NONE' and not report['execution_authorized']
    assert RELEASE_PROMOTIONS == ()
    assert 'NO_SHARED_CAPITAL_PORTFOLIO_REPLAY' in report['deployment_blockers']
    assert all(x['metrics'] is None for x in report['results']
               if x['policy_id'] == 'NOISE_BAND_LONG_V1' and x['symbol'] not in ('SPY', 'QQQ'))


def test_plan_and_source_changes_are_rejected_before_input_access(tmp_path):
    plan = v.frozen_plan(); plan['plan'] = deepcopy(plan['plan'])
    plan['plan']['screen_min_win_rate'] = '0.01'
    with pytest.raises(ValueError, match='plan or source changed'): v.evaluate(tmp_path, plan)
    plan = v.frozen_plan(); plan['source_hashes']['equity_overhaul/replay.py'] = '0'*64
    with pytest.raises(ValueError, match='plan or source changed'): v.evaluate(tmp_path, plan)


def test_positive_mock_returns_cannot_hide_missing_symbols_or_promote(tmp_path):
    dataset(tmp_path, session())
    fake = {'unresolved_positions': 0, 'trades': [
        {'entry_at': '2026-07-01T14:00:00+00:00', 'net_r': '2'} for _ in range(100)]}
    with patch.object(v, 'replay', return_value=fake):
        report = v.evaluate(tmp_path, v.frozen_plan())
    cell = next(x for x in report['aggregates'] if x['window'] == 'holdout' and
                x['policy_id'] == 'ORB5_LONG_V1' and x['cost'] == 'normal')
    assert cell['metrics']['win_rate'] == '1'
    assert not cell['research_screen_pass']
    assert 'QQQ:INSUFFICIENT_SESSION_COVERAGE' in cell['blockers']
    assert report['promotion'] == 'NONE'
