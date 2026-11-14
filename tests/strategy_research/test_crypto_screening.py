from dataclasses import replace
import math

import pytest

from app.strategy_research.crypto_screening import (
    Bar, CANDIDATES, EXECUTION_AUTHORIZED, daily, epoch, replay, screen, signals, simulate, validate,
)


def candles(n=260):
    result=[]
    for i in range(n):
        p=100+i*.1+math.sin(i/3)*4
        result.append(Bar(i*21600,p,p+2,p-2,p+math.sin(i),10.))
    return tuple(result)


@pytest.mark.parametrize('candidate',CANDIDATES)
def test_future_candles_cannot_change_earlier_signals(candidate):
    bars=candles()
    full=signals(bars,candidate)
    for n in (200,201,213,237,259):
        assert signals(bars[:n],candidate)==full[:n]
    # Extreme future price cannot alter history, including cloud or RSI history.
    extreme=bars[:237]+tuple(replace(b,open=1e6,high=2e6,low=1.,close=1e6) for b in bars[237:])
    assert signals(extreme,candidate)[:237]==full[:237]


def run(bars, decisions, **kwargs):
    return simulate(bars, decisions, seconds=21600,start=0,end=bars[-1].start+21600,
                    candidate='RSI2_TREND',**kwargs)


def test_completed_signal_fills_next_open_and_charges_both_sides():
    bars=(Bar(0,100,110,95,100,1),Bar(21600,110,112,105,110,1),
          Bar(43200,120,122,115,120,1))
    result=run(bars,((True,False,90),(False,True,90),(False,False,90)))
    t=result['trades'][0]
    assert t['entry_at']==21600 and t['exit_at']==43200
    assert t['entry']==pytest.approx(110*1.001)
    assert t['exit']==pytest.approx(120*.999)
    assert t['pnl']==pytest.approx(t['quantity']*(120*.999*.999-110*1.001*1.001))
    assert t['quantity']*t['entry']*1.001<=2000.000001
    assert t['quantity']*(t['entry']-90)<=50.000001


def test_gap_stop_uses_worse_open_and_last_open_is_not_a_win():
    bars=(Bar(0,100,105,95,100,1),Bar(21600,100,106,95,105,1),
          Bar(43200,80,85,75,82,1))
    d=((True,False,90),(False,False,90),(False,False,90))
    prefix=run(bars[:2],d[:2])
    assert prefix['open_positions']==1 and prefix['closed_trades']==0 and prefix['win_rate'] is None
    result=run(bars,d)
    assert result['trades'][0]['reason']=='GAP_STOP'
    assert result['trades'][0]['exit']==pytest.approx(80*.999)
    assert result['wins']==0


def test_stop_wins_over_target_on_ambiguous_bar():
    bars=(Bar(0,100,105,95,100,1),Bar(21600,100,106,95,105,1),
          Bar(43200,100,120,80,110,1))
    r=simulate(bars,((True,False,90),(False,False,90),(False,False,90)),seconds=21600,
               start=0,end=64800,candidate='BB_RSI14_REVERSION')
    assert r['trades'][0]['reason']=='STOP' and r['wins']==0


def test_entry_bar_stop_and_gap_through_setup_stop():
    bars=(Bar(0,100,105,95,100,1),Bar(21600,100,106,80,90,1))
    d=((True,False,95),(False,False,95))
    r=run(bars,d)
    assert r['trades'][0]['reason']=='ENTRY_BAR_STOP'
    changed=bars[:1]+(Bar(21600,90,105,85,100,1),)
    assert run(changed,d)['closed_trades']==0
    assert run(changed,d)['open_positions']==0


def test_future_partial_bar_cannot_execute_or_mark():
    bars=candles(8)
    d=tuple((True,False,90) for _ in bars)
    full=simulate(bars,d,seconds=21600,start=0,end=3*21600,candidate='RSI2_TREND')
    prefix=run(bars[:3],d[:3])
    assert full==prefix


def test_invalid_or_missing_history_is_not_silently_filled():
    bars=candles(8)
    for invalid in (bars[:2]+bars[3:],bars[:2]+bars[1:],(replace(bars[0],high=1),),
                    (replace(bars[0],close=float('nan')),)):
        with pytest.raises(ValueError):validate(invalid,21600)
    assert len(daily(bars))==2
    with pytest.raises(ValueError):daily(bars[:-1])


def test_fixed_screen_reports_every_candidate_pair_timeframe_window_cost_and_never_activates():
    start=epoch('2022-01-01')
    bars=tuple(replace(b,start=b.start+start) for b in candles(800))
    result=screen({'BTC-USD':bars,'ETH-USD':bars})
    assert len(result['rows'])==2*2*4*4*2
    assert len(result['gates'])==8
    assert not any(g['research_eligible'] for g in result['gates'])
    assert result['execution_authorized'] is EXECUTION_AUTHORIZED is False
    for row in result['rows']:
        assert row['closed_trades']==len(row['trades'])


def test_rsi2_rule_matches_independent_scalar_wilder_oracle():
    bars=candles()
    values=[b.close for b in bars]
    actual=signals(bars,'RSI2_TREND')
    for n in (200,213,237,260):
        diffs=[values[i]-values[i-1] for i in range(1,n)]
        up=sum(max(x,0) for x in diffs[:2])/2
        down=sum(max(-x,0) for x in diffs[:2])/2
        for change in diffs[2:]:
            up=(up+max(change,0))/2
            down=(down+max(-change,0))/2
        level=100-100/(1+up/down) if down else 100 if up else 50
        expect=values[n-1]>sum(values[n-200:n])/200 and level<=10
        assert actual[n-1][0]==expect
        assert actual[n-1][1]==(values[n-1]>sum(values[n-5:n])/5)


def test_entry_bar_target_and_fee_inclusive_initial_risk_bound():
    bars=(Bar(0,100,105,95,100,1),Bar(21600,100,120,95,115,1))
    r=simulate(bars,((True,False,90),(False,False,90)),seconds=21600,start=0,end=43200,
               candidate='BB_RSI14_REVERSION')
    t=r['trades'][0]
    assert t['reason']=='ENTRY_BAR_TARGET'
    risk=t['quantity']*(t['entry']*1.001-90*.999*.999)
    assert risk<=50.000001
