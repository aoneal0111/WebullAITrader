import numpy as np
import pandas as pd
import pytest
pytest.importorskip('numba',reason='Optional accelerated research environment required')
from app.strategy_lab.daily_income_lab import Spec,engine,features,grid,metrics,signals


def replay(prices, buy_at=(0,), sell_at=(), *, fee=0., slip=0., stop=1., target=1., hold=100):
    values=np.array(prices,dtype=float)[:,None,:]
    n=len(values);buy=np.zeros((n,1),dtype=bool);sell=buy.copy()
    buy[list(buy_at),0]=True;sell[list(sell_at),0]=True
    atr=np.full((n,1),5.)
    return engine(values,atr,buy,sell,np.arange(n,dtype=np.int64),n,
                  1000.,fee,slip,1,stop,target,hold)


def test_next_open_entry_and_stop_precedes_target():
    r=replay([[100,101,99,100,100000],[100,110,90,100,100000]])
    assert r[0][0]==1 and r[0][1]==1
    assert r[0][5]==pytest.approx(-50.)
    assert r[0][2]==0


def test_entry_uses_next_open_and_previous_atr():
    r=replay([[100,101,99,100,100000],[120,126,119,125,100000]],stop=10.,target=1.)
    assert r[0][5]==pytest.approx(1000/120*5,abs=1e-5)


def test_signal_exit_waits_for_next_open():
    r=replay([[100,101,99,100,100000],[100,200,99,200,100000],
              [110,111,109,110,100000]],sell_at=(1,),stop=100.,target=100.)
    assert r[0][5]==pytest.approx(100.)
    assert r[0][8]==0


def test_last_bar_signal_has_no_fill():
    r=replay([[100,101,99,100,100000]])
    assert r[0][0]==0 and r[0][5]==0


def test_missing_next_bar_does_not_fill_later():
    r=replay([[100,101,99,100,100000],[np.nan]*5,[120,121,119,120,100000]])
    assert r[0][0]==0


def test_round_trip_fees_and_slippage_reduce_cash():
    r=replay([[100,101,99,100,100000],[100,101,99,100,100000]],fee=.004,slip=.001,stop=100.,target=100.)
    qty=np.floor(1000/(100*1.001*1.004)*1e8)/1e8
    cost=qty*100*1.001*1.004
    expected=1000-cost+qty*100*.999*.996
    assert r[0][6]==pytest.approx(expected)
    assert r[0][8]==1 and r[0][11]<1e-8


def test_100_dollars_is_per_day_after_overhead_not_total():
    r=replay([[100,101,99,100,100000],[100,110,99,110,100000]],stop=100.,target=100.)
    m=metrics(r,1000.,overhead=2.,target=100.)
    assert m['terminal_net_after_overhead']==pytest.approx(96.)
    assert not m['average_target_met'] and m['days_at_or_above_target']==0


def test_grid_contains_720_distinct_specs():
    specs=grid();assert len(specs)==720
    assert len({s.name for s in specs})==720
    assert len({s.family for s in specs})==16


def test_trailing_stop_uses_close_and_takes_effect_next_bar():
    values=np.array([[100,101,99,100,100000],[100,200,99,120,100000],
                     [100,101,95,100,100000]],dtype=float)[:,None,:]
    buy=np.array([[True],[False],[False]]);sell=np.zeros((3,1),dtype=bool)
    r=engine(values,np.full((3,1),5.),buy,sell,np.arange(3,dtype=np.int64),3,
             1000.,0.,0.,1,10.,0.,100,trail_mult=3.)
    # No target at the entry price, and no retroactive stop at this bar's high.
    assert r[0][0]==1 and r[0][1]==1 and r[0][8]==0
    assert r[1][1]==pytest.approx(1200.)
    assert r[0][5]==pytest.approx(0.)


def test_all_signal_families_are_prefix_causal():
    rng=np.random.default_rng(42);n=1450
    close=100*np.exp(np.cumsum(rng.normal(0,.02,n)))
    raw=pd.DataFrame(dict(date=pd.date_range('2020-01-01',periods=n,freq='h',tz='UTC'),
        open=close,high=close*1.03,low=close*.97,close=close,volume=np.full(n,1000.)))
    full=features(raw);prefix=features(raw.iloc[:1300])
    full['eligible']=True;prefix['eligible']=True
    for family in sorted({s.family for s in grid()}):
        spec=next(s for s in grid() if s.family==family)
        b,s=signals(full,spec);bp,sp=signals(prefix,spec)
        np.testing.assert_array_equal(b[:1300],bp)
        np.testing.assert_array_equal(s[:1300],sp)


@pytest.mark.parametrize('seed',range(10))
def test_accelerated_wallet_matches_independent_reference_replay(seed):
    from app.strategy_lab.broad_strategy_lab import Case,replay as reference
    rng=np.random.default_rng(seed);n,m=180,4
    dates=list(pd.date_range('2024-01-01',periods=n,freq='h',tz='UTC'))
    close=100*np.exp(np.cumsum(rng.normal(0,.015,(n,m)),axis=0))
    opens=np.vstack([close[0],close[:-1]])*(1+rng.normal(0,.002,(n,m)))
    high=np.maximum(opens,close)*1.01;low=np.minimum(opens,close)*.99
    vol=np.full((n,m),1e9);values=np.stack([opens,high,low,close,vol],axis=-1)
    buys=rng.random((n,m))<.12;sells=rng.random((n,m))<.12;atr=np.full((n,m),2.)
    bars={at:{str(j):dict(zip(('open','high','low','close','volume'),values[t,j]))
             for j in range(m)} for t,at in enumerate(dates)}
    configs={str(j):dict(stop_atr=3.,target_atr=4.,trail_atr=None,timeout=6) for j in range(m)}
    decisions={str(j):{at:(buys[t,j],sells[t,j],2.) for t,at in enumerate(dates)} for j in range(m)}
    r=reference({},Case('IGNORED',0),dates,fee=.004,slip=.0005,initial=10000,slots=2,
                min_notional=10,prepared=(bars,configs,decisions))
    a=engine(values,atr,buys,sells,np.arange(n,dtype=np.int64),n,10000.,.004,.0005,2,3.,4.,6)
    liquidation=r['marked_net']-(r['ending_marked_equity']-r['ending_cash'])*(.004+.0005-.004*.0005)
    assert a[0][0]==r['entries'] and a[0][1]==r['closed_trades'] and a[0][2]==r['wins']
    assert a[0][5]==pytest.approx(liquidation,abs=1e-5)
