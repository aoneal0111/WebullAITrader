from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
import random
from unittest.mock import patch

import pytest

from app.equity_overhaul.reference import reference_trades, SPEC
from app.equity_overhaul.local_audit import compare, run_local_audit, source_matches
from app.equity_overhaul.validation import PLAN, frozen_plan, schedule
from app.equity_overhaul.replay import replay
from app.strategy_research.candidates import Bar
from app.strategy_research.replay import Costs

START = datetime(2026, 7, 1, 13, 30, tzinfo=UTC)


def session(n=90, start=START):
    end = start+timedelta(minutes=n)
    return tuple(Bar('SPY', start+timedelta(minutes=i), start+timedelta(minutes=i+1),
        D(100), D('100.2'), D('99.8'), D('100.1'), D(1000), end) for i in range(n))


def orb_session():
    bars = list(session()); bars[5] = replace(bars[5], close=D('100.4'), high=D('100.5'))
    bars[6] = replace(bars[6], open=D('100.4'), close=D(102), high=D(103), low=D(100))
    return tuple(bars)


def ref(bars, policy='ORB5_LONG_V1', bps=D(0), fee=D(0)):
    return reference_trades(bars, policy, bps, fee, date(2026, 7, 1), date(2026, 7, 2))


def test_reference_target_matches_hand_calculation_and_preserves_stop_first():
    bars = orb_session(); trade = ref(bars)[0]
    stop = D('99.8')*D('.9995'); risk = D('100.4')-stop
    assert trade['entry_at'] == bars[6].opened.isoformat()
    assert D(trade['stop']) == stop and D(trade['exit']) == D('100.4')+2*risk
    assert trade['reason'] == 'TARGET' and D(trade['net_r']) == 2
    both = bars[:6]+(replace(bars[6], low=D(98)),)+bars[7:]
    assert ref(both)[0]['reason'] == 'STOP_FIRST'
    assert D(ref(both)[0]['net_r']) == -1


def test_reference_gap_stop_and_both_side_costs_are_not_optimistic():
    bars = list(orb_session())
    bars[6] = replace(bars[6], high=D('100.5'), close=D('100.4'))
    bars[7] = replace(bars[7], open=D(97), high=D(98), low=D(96), close=D(97))
    trade = ref(tuple(bars), bps=D(5), fee=D('.0035'))[0]
    assert trade['reason'] == 'GAP_STOP' and trade['exit_at'] == bars[7].opened.isoformat()
    assert D(trade['entry']) == D('100.4')*D('1.0005')
    assert D(trade['exit']) == D(97)*D('.9995')
    assert D(trade['net_pnl_per_share']) == D(trade['exit'])-D(trade['entry'])-D('.007')


def test_scalper_time_exit_next_open_and_cost_sensitive_loss():
    bars = list(session())
    bars[20] = replace(bars[20], close=D('99.8'), low=D('99.6'), high=D('100.1'))
    bars[21] = replace(bars[21], close=D('100.5'), high=D('100.6'))
    a = ref(tuple(bars), 'VWAP_RECLAIM_SCALP_V1')[0]
    b = ref(tuple(bars), 'VWAP_RECLAIM_SCALP_V1', D(5), D('.0035'))[0]
    assert a['entry_at'] == bars[22].opened.isoformat() and a['exit_at'] == bars[26].closed.isoformat()
    assert a['reason'] == 'TIME_EXIT' and D(a['net_r']) > 0 and D(b['net_r']) < 0
    bars[22] = replace(bars[22], volume=D(0))
    assert ref(tuple(bars), 'VWAP_RECLAIM_SCALP_V1')[0] == a


def test_noise_band_warmup_completed_clock_and_signal_exit_next_open():
    prior = tuple(b for day in range(14) for b in session(start=START-timedelta(days=14-day)))
    current = list(session()); current[29] = replace(current[29], close=D(103), high=D('103.1'))
    current[30] = replace(current[30], open=D(103), high=D('103.2'), low=D('102.8'), close=D(103))
    trade = ref(prior+tuple(current), 'NOISE_BAND_LONG_V1')[0]
    assert trade['signal_at'] == current[29].closed.isoformat()
    assert trade['entry_at'] == current[30].opened.isoformat()
    assert trade['exit_at'] == current[60].opened.isoformat() and trade['reason'] == 'SIGNAL_EXIT'
    assert ref(prior[90:]+tuple(current), 'NOISE_BAND_LONG_V1') == []
    assert ref(tuple(replace(b, symbol='TSLA') for b in prior+tuple(current)), 'NOISE_BAND_LONG_V1') == []


@pytest.mark.parametrize('policy', tuple(SPEC))
@pytest.mark.parametrize('bps,fee', [(D(0),D(0)), (D(5),D('.0035')), (D(10),D('.007'))])
def test_independent_reference_agrees_with_existing_replay_on_seeded_sessions(policy, bps, fee):
    rng = random.Random(825); all_bars = []
    for day in range(17):
        bars = list(session(start=START-timedelta(days=16-day)))
        price = D(100)
        for i, b in enumerate(bars):
            previous = price; price += D(rng.choice([-9,-4,-1,2,5,11]))/100
            bars[i] = replace(b, open=previous, close=price,
                high=max(previous,price)+D('.07'), low=min(previous,price)-D('.08'),
                volume=D(rng.choice([0,500,1000,3000])))
        all_bars.extend(bars)
    bars = tuple(all_bars)
    expected = replay(bars, policy, Costs(bps, fee), evaluate_from=date(2026,7,1), evaluate_until=date(2026,7,2))['trades']
    actual = ref(bars, policy, bps, fee)
    assert compare(expected, actual)['mismatched_records'] == 0
    # Future bars cannot change any result in an already-ended date window.
    future = session(start=START+timedelta(days=1))
    assert ref(bars+future, policy, bps, fee) == actual


def test_comparison_detects_missing_trades_bad_price_and_bad_exit():
    original = ref(orb_session()); altered = deepcopy(original)
    assert compare(original, original)['mismatched_records'] == 0
    assert compare(original, [])['mismatched_records'] == 1
    altered[0]['exit'] = str(D(altered[0]['exit'])+D('.01'))
    altered[0]['reason'] = 'TIME_EXIT'
    mismatch = compare(original, altered)
    assert set(mismatch['examples'][0]['fields']) == {'exit', 'reason'}


def test_local_audit_rejects_bad_identity_before_reading_large_files(tmp_path):
    (tmp_path/'manifest.json').write_text('{}')
    (tmp_path/'report.json').write_text('{"plan_document":{}}')
    with pytest.raises((ValueError,KeyError)):
        run_local_audit(tmp_path, tmp_path/'report.json')
    assert not source_matches({})
    plan = frozen_plan(); assert source_matches(plan['source_hashes'])
    plan['source_hashes']['equity_overhaul/replay.py'] = '0'*64
    assert not source_matches(plan['source_hashes'])


def test_end_to_end_audit_keeps_output_small_and_never_promotes(tmp_path):
    # A complete real session for every symbol. The frozen long windows remain
    # intentionally incomplete, so this is mechanics, not financial evidence.
    start, end = schedule(date(2026,7,1),date(2026,7,2))[date(2026,7,1)]
    bars = list(session(n=390,start=start))
    bars[5] = replace(bars[5], close=D('100.4'), high=D('100.5'))
    bars[6] = replace(bars[6], open=D('100.4'), close=D(102), high=D(103), low=D(100))
    bars = tuple(bars)
    inputs = []; cells = []
    for symbol in PLAN['symbols']:
        records = tuple(replace(b,symbol=symbol) for b in bars)
        raw = json.dumps([asdict(b) for b in records], default=str).encode()
        name = symbol+'-normalized.json'; (tmp_path/name).write_bytes(raw)
        inputs.append({'file':name,'sha256':sha256(raw).hexdigest()})
        for window in PLAN['windows']:
            for policy in SPEC:
                for cost,bps,fee in [('normal',D(5),D('.0035')),('stress',D(10),D('.007'))]:
                    applicable = policy != 'NOISE_BAND_LONG_V1' or symbol in ('SPY','QQQ')
                    trades = reference_trades(records,policy,bps,fee,
                        date.fromisoformat(window['start']),date.fromisoformat(window['end_exclusive'])) if applicable else []
                    cells.append({'window':window['name'],'symbol':symbol,'policy_id':policy,
                                  'cost':cost,'applicable':applicable,'trades':trades})
    manifest = {'plan_document':frozen_plan(),'normalized_inputs':inputs}
    raw = json.dumps(manifest).encode(); (tmp_path/'manifest.json').write_bytes(raw)
    report = {'scope':'OFFLINE_EQUITY_TEMPORAL_SCREEN','plan_document':manifest['plan_document'],
              'data':{'manifest_sha256':sha256(raw).hexdigest(),
                      'normalized_inputs':{x['file']:x['sha256'] for x in inputs}},'results':cells}
    (tmp_path/'report.json').write_text(json.dumps(report))
    out = run_local_audit(tmp_path,tmp_path/'report.json',progress=lambda *a,**kw:None)
    assert out['mismatched_cells'] == 0 and out['comparison_cells'] == 108
    assert out['status'] == 'ALL_REPORTED_TRADES_MATCH_REFERENCE'
    assert not out['execution_authorized'] and out['promotion'] == 'NONE'
    encoded = json.dumps(out)
    assert len(encoded) < 100_000 and '"trades": [' not in encoded and '"bars"' not in encoded
    assert len(out['aggregates']) == 36
    assert any(c['expected_trades'] > 0 for c in out['comparisons'])
    # A wrong recorded exit must produce a small failure summary, not a pass.
    tampered = deepcopy(report)
    changed = next(c for c in tampered['results'] if c['trades'])
    changed['trades'][0]['reason'] = 'WRONG_EXIT'
    (tmp_path/'report.json').write_text(json.dumps(tampered))
    bad = run_local_audit(tmp_path,tmp_path/'report.json',progress=lambda *a,**kw:None)
    assert bad['status'] == 'REPLAY_MISMATCH' and bad['mismatched_cells'] == 1
    # A modified candle file cannot pass on a self-declared manifest alone.
    with (tmp_path/'SPY-normalized.json').open('ab') as f: f.write(b' ')
    with pytest.raises(ValueError,match='hash mismatch'):
        run_local_audit(tmp_path,tmp_path/'report.json',progress=lambda *a,**kw:None)
