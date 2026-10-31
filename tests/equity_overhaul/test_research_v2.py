from dataclasses import replace, asdict
from datetime import UTC,date,datetime,timedelta
from decimal import Decimal as D
from hashlib import sha256
import json

import pytest

from app.equity_overhaul.research_v2 import prepare,activity_selection,trades,POLICIES,experiment_plan,plan_hash
from app.equity_overhaul.v2_screen import run_screen
from app.equity_overhaul.validation import PLAN,SYMBOLS,frozen_plan,schedule
from app.equity_overhaul.admission import EquityQualificationPolicy,RELEASE_PROMOTIONS
from app.strategy_research.candidates import Bar

START=datetime(2026,7,1,13,30,tzinfo=UTC)


def session(symbol='AAPL',start=START,n=390,volume=D(1000)):
    end=start+timedelta(minutes=n)
    return tuple(Bar(
        symbol,start+timedelta(minutes=i),start+timedelta(minutes=i+1),
        D(100),D('100.2'),D('99.8'),D('100.1'),volume,end) for i in range(n))


def with_prior(current):
    return tuple(b for d in range(14) for b in session(current[0].symbol,START-timedelta(days=14-d),len(current)))+tuple(current)


def orb_data():
    data={}
    for symbol,mult in [('AAPL',4),('MSFT',3),('NVDA',2),('TSLA',1)]:
        bars=list(session(symbol,volume=D(1000*mult)))
        # First eligible post-range minute triggers the resting breakout order.
        bars[5]=replace(bars[5],high=D(102),low=D(100),close=D('101.5'))
        bars[-1]=replace(bars[-1],open=D(104),high=D(106),low=D(103),close=D(105))
        # Later bars remain above the initial opening-low stop.
        data[symbol]=with_prior(tuple(bars))
    return data


def test_activity_selection_uses_opening_volume_only_and_deterministic_ties():
    data=orb_data();ready=prepare(data)
    assert activity_selection(ready,date(2026,7,1))==('AAPL','MSFT')
    altered=dict(data);bars=list(altered['TSLA'])
    bars[-1]=replace(bars[-1],volume=D(999999999))
    altered['TSLA']=tuple(bars)
    assert activity_selection(prepare(altered),date(2026,7,1))==('AAPL','MSFT')
    # Ranking is relative to each symbol's own prior opening volumes.
    for symbol in data:
        data[symbol]=tuple(replace(b,volume=D(1000)) for b in data[symbol])
    assert activity_selection(prepare(data),date(2026,7,1))==('AAPL','MSFT')
    assert activity_selection(prepare({'AAPL':session()}),date(2026,7,1))==()


def test_orb_enters_known_breakout_level_and_runs_past_sixty_minutes_to_close():
    ready=prepare(orb_data());records,_=trades(ready,'ORB5_ACTIVITY_RUNNER_V2',D(0),D(0),date(2026,7,1),date(2026,7,2))
    assert len(records)==2 and {t['symbol'] for t in records}=={'AAPL','MSFT'}
    for t in records:
        assert D(t['entry'])==D('100.2')
        assert t['signal_at']==(START+timedelta(minutes=5)).isoformat()
        assert t['entry_at']==(START+timedelta(minutes=5)).isoformat()
        assert t['reason']=='SESSION_END' and t['exit_at']==(START+timedelta(minutes=390)).isoformat()
        assert D(t['net_r'])>2


def test_orb_same_bar_stop_first_gap_entry_and_both_side_fees():
    data=orb_data()
    for symbol in data:
        bars=list(data[symbol]);idx=14*390+5
        bars[idx]=replace(bars[idx],open=D(101),low=D(98),high=D(102),close=D(100))
        data[symbol]=tuple(bars)
    rs,_=trades(prepare(data),'ORB5_ACTIVITY_RUNNER_V2',D(5),D('.0035'),date(2026,7,1),date(2026,7,2))
    assert len(rs)==2
    t=rs[0];assert t['reason']=='STOP_FIRST' and D(t['entry'])==D(101)*D('1.0005')
    assert D(t['exit'])==D('99.8')*D('.9995')*D('.9995')
    assert D(t['net_pnl_per_share'])==D(t['exit'])-D(t['entry'])-D('.007')


def test_noise_wider_known_session_stop_and_completed_clock_exit():
    current=list(session('SPY'))
    current[0]=replace(current[0],low=D(98))
    current[29]=replace(current[29],close=D(103),high=D('103.1'))
    current[30]=replace(current[30],open=D(103),high=D('103.2'),low=D('102.8'),close=D(103))
    # A move through V1's tight five-bar stop must not silently close V2.
    current[31]=replace(current[31],low=D(99),high=D('100.2'),close=D('100.1'))
    ready=prepare({'SPY':with_prior(tuple(current))})
    rs,_=trades(ready,'NOISE_BAND_CLOCK_V2',D(0),D(0),date(2026,7,1),date(2026,7,2))
    t=rs[0]
    assert D(t['stop'])==D(98)*D('.9995')
    assert t['entry_at']==current[30].opened.isoformat()
    assert t['reason']=='CLOCK_EXIT' and t['exit_at']==current[60].opened.isoformat()


def test_noise_gap_stop_precedes_pending_clock_exit_and_future_low_cannot_widen_stop():
    current=list(session('SPY'))
    current[29]=replace(current[29],close=D(103),high=D('103.1'))
    current[60]=replace(current[60],open=D(97),high=D(98),low=D(96),close=D(97))
    ready=prepare({'SPY':with_prior(tuple(current))})
    rs,_=trades(ready,'NOISE_BAND_CLOCK_V2',D(0),D(0),date(2026,7,1),date(2026,7,2))
    t=rs[0];assert t['reason']=='GAP_STOP' and t['exit']=='97'
    assert D(t['stop'])==D('99.8')*D('.9995')


def test_empty_warmup_no_orders_future_invariance_and_plan_copy():
    data=orb_data();ready=prepare(data)
    a,_=trades(ready,'ORB5_ACTIVITY_RUNNER_V2',D(0),D(0),date(2026,7,1),date(2026,7,2))
    for symbol in data:data[symbol]+=session(symbol,START+timedelta(days=1))
    b,_=trades(prepare(data),'ORB5_ACTIVITY_RUNNER_V2',D(0),D(0),date(2026,7,1),date(2026,7,2))
    assert a==b
    p=experiment_plan();old=plan_hash();p['policies'].clear();assert plan_hash()==old
    assert RELEASE_PROMOTIONS==()
    for policy in POLICIES:assert not EquityQualificationPolicy().permits({'strategy_policy_version':policy})


def test_v2_local_screen_keeps_every_cell_small_and_never_promotes(tmp_path):
    opened,closed=schedule(date(2026,7,1),date(2026,7,2))[date(2026,7,1)]
    inputs=[]
    for symbol in SYMBOLS:
        raw=json.dumps([asdict(b) for b in session(symbol,opened)],default=str).encode()
        name=symbol+'-normalized.json';(tmp_path/name).write_bytes(raw)
        inputs.append({'file':name,'sha256':sha256(raw).hexdigest()})
    manifest={'source':'SYNTHETIC_TEST_ONLY','plan_document':frozen_plan(),'normalized_inputs':inputs}
    raw=json.dumps(manifest).encode();(tmp_path/'manifest.json').write_bytes(raw)
    report={'scope':'OFFLINE_EQUITY_TEMPORAL_SCREEN','plan_document':manifest['plan_document'],'data':{'manifest_sha256':sha256(raw).hexdigest(),
            'normalized_inputs':{x['file']:x['sha256'] for x in inputs}}}
    (tmp_path/'report.json').write_text(json.dumps(report))
    out=run_screen(tmp_path,tmp_path/'report.json',progress=lambda *a,**k:None)
    assert len(out['results'])==72 and len(out['aggregates'])==24
    assert len(json.dumps(out))<100000 and not out['execution_authorized'] and out['promotion']=='NONE'
    assert 'NO_UNSEEN_VALIDATION' in out['deployment_blockers']
    assert all(c['trades']==0 for c in out['aggregates'])
    with (tmp_path/'SPY-normalized.json').open('ab') as f:f.write(b' ')
    with pytest.raises(ValueError,match='hash mismatch'):run_screen(tmp_path,tmp_path/'report.json')
