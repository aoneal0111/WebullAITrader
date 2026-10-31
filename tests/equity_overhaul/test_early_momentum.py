from dataclasses import replace
from datetime import date,timedelta
from decimal import Decimal as D
import json

from app.equity_overhaul.early_momentum import setup,replay,POLICY,specification,fingerprint
from app.equity_overhaul.entry_timing_screen import run_screen
from app.equity_overhaul.research_v2 import prepare
from app.equity_overhaul.admission import production_policy
from tests.equity_overhaul.test_research_v2 import session,START
from tests.equity_overhaul.test_history_crosscheck import fixture


def current():
    bars=[replace(b,open=D('5.45'),high=D('5.5'),low=D('5.4'),close=D('5.45')) for b in session('AAPL')]
    for i,c,h in [(2,'5.1','5.2'),(3,'5.3','5.4'),(4,'5.55','5.6')]:
        bars[i]=replace(bars[i],open=D(c)-D('.05'),close=D(c),high=D(h),low=D(c)-D('.1'))
    bars[5]=replace(bars[5],open=D('5.48'),high=D('5.5'),low=D('5.4'),close=D('5.45'))
    bars[6]=replace(bars[6],open=D('5.47'),high=D('5.53'),low=D('5.43'),close=D('5.52'))
    bars[7]=replace(bars[7],open=D('5.53'),high=D('5.64'),low=D('5.52'),close=D('5.6'))
    bars[8]=replace(bars[8],open=D('5.55'),high=D('5.6'),low=D('5.50'),close=D('5.52'))
    bars[9]=replace(bars[9],open=D('5.4'),high=D('5.5'),low=D('5.30'),close=D('5.4'))
    return bars


def ready(bars):
    prior=tuple(replace(b,open=D(5),high=D('5.1'),low=D('4.9'),close=D(5))
        for b in session('AAPL',START-timedelta(days=1)))
    return prepare({'AAPL':prior+tuple(bars)})


def records(bars,mode='HALF_1R_HALF_2R',bps=D(0),fee=D(0)):
    return replay(ready(bars),mode,bps,fee,date(2026,7,1),date(2026,7,2))[0]


def test_arm_below_hod_before_trigger_and_without_waiting_for_recovery_close():
    bars=current();s=setup(bars,5,D(5))
    assert s['trigger']==D('5.51')<s['hod']==D('5.6') and s['stop']==D('5.39')
    # The signal is defined before the entry candle exists, irrespective of its closing price.
    assert setup(bars[:6],5,D(5))==s
    bars[6]=replace(bars[6],close=D('5.48'))
    t=records(bars)[0]
    assert t['entry']=='5.51' and t['entry_at']==bars[6].opened.isoformat()
    assert t['signal_at']==bars[5].closed.isoformat()
    assert t['owner']=='WARRIOR_MOMENTUM'


def test_partials_keep_some_profit_in_same_path_that_full_position_loses():
    bars=current();partial=records(bars)[0];full=records(bars,'FULL_2R')[0]
    assert partial['partial_taken'] and D(partial['net_r'])==D('.5')
    assert partial['reason']=='STOP_FIRST' and D(full['net_r'])==D(-1)
    costed=records(bars,bps=D(5),fee=D('.0035'))[0]
    assert D(costed['net_r'])<D(partial['net_r'])


def test_stop_first_and_no_retroactive_breakeven_within_partial_candle():
    bars=current();bars[6]=replace(bars[6],low=D('5.3'))
    t=records(bars)[0];assert t['net_r']=='-1' and not t['partial_taken']
    bars=current();bars[7]=replace(bars[7],low=D('5.4'))
    bars[8]=replace(bars[8],low=D('5.52'),high=D('5.8'))
    t=records(bars)[0];assert t['reason']=='TARGET_2R' and D(t['net_r'])==D('1.5')


def test_no_chasing_gap_at_hod_no_automatic_buy_on_ten_percent_move():
    bars=current();bars[6]=replace(bars[6],open=D('5.7'),high=D('5.8'),low=D('5.6'),close=D('5.7'))
    assert records(bars)==[]
    assert setup(current(),5,D('5.5')) is None
    # A strong upmove alone with no pullback cannot arm an entry.
    bars=current();bars[5]=replace(bars[5],open=D('5.5'),close=D('5.58'),high=D('5.59'))
    assert setup(bars,5,D(5)) is None


def test_window_end_future_invariance_plan_identity_and_no_order_authority():
    bars=current();a=records(bars)
    prepared=ready(bars);prepared['AAPL'].update(prepare({'AAPL':session('AAPL',START+timedelta(days=1))})['AAPL'])
    b,_=replay(prepared,'HALF_1R_HALF_2R',D(0),D(0),date(2026,7,1),date(2026,7,2))
    assert a==b
    h=fingerprint();specification()['management'].clear();assert fingerprint()==h
    assert not production_policy().permits({'strategy_policy_version':POLICY,'strategy_owner':'WARRIOR_MOMENTUM'})


def test_local_screen_retains_all_periods_costs_modes_and_empty_cells(tmp_path):
    path=fixture(tmp_path);out=run_screen(tmp_path,path,progress=lambda *a,**k:None)
    assert len(out['aggregates'])==24 and len(out['results'])==144
    assert len(json.dumps(out))<100000
    assert all(a['trades']==0 for a in out['aggregates'])
    assert out['promotion']=='NONE' and not out['execution_authorized']
