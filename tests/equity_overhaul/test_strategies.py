from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from unittest.mock import patch

import pytest

from app.equity_overhaul.strategies import signal,RULES,ReplacementSignal,vwap
from app.equity_overhaul.replay import replay
from app.strategy_research.candidates import Bar
from app.strategy_research.replay import Costs

START=datetime(2026,10,15,13,30,tzinfo=UTC)


def session(n=60,start=START,symbol='SPY'):
    end=start+timedelta(minutes=n)
    return tuple(Bar(symbol,start+timedelta(minutes=i),start+timedelta(minutes=i+1),
        D(100),D('100.2'),D('99.8'),D('100.1'),D(1000),end) for i in range(n))


def test_orb_requires_complete_bullish_opening_range_and_first_cross():
    bars=session()
    assert signal(bars[:5],'ORB5_LONG_V1',at=bars[4].closed) is None
    trigger=replace(bars[5],close=D('100.4'),high=D('100.5'))
    prefix=bars[:5]+(trigger,)
    s=signal(prefix,'ORB5_LONG_V1',at=trigger.closed)
    assert s is not None and s.stop==D('99.8')*D('.9995')
    assert not s.execution_authorized
    continued=prefix+(replace(bars[6],close=D('100.5'),high=D('100.6')),)
    assert signal(continued,'ORB5_LONG_V1',at=bars[6].closed) is None
    bearish=(replace(bars[0],open=D('100.2')),)+prefix[1:]
    assert signal(bearish,'ORB5_LONG_V1',at=trigger.closed) is None


def test_scalper_requires_vwap_tag_reclaim_and_rising_trend():
    bars=list(session())
    bars[20]=replace(bars[20],open=D(100),close=D('99.8'),low=D('99.6'),high=D('100.1'))
    bars[21]=replace(bars[21],close=D('100.5'),high=D('100.6'))
    s=signal(tuple(bars[:22]),'VWAP_RECLAIM_SCALP_V1',at=bars[21].closed)
    assert s is not None and s.max_hold==5 and s.target_r==1
    bars[21]=replace(bars[21],close=D('99.9'))
    assert signal(tuple(bars[:22]),'VWAP_RECLAIM_SCALP_V1',at=bars[21].closed) is None


def test_noise_band_uses_fourteen_prior_completed_sessions_at_same_clock_only():
    history=tuple(b for d in range(14) for b in session(start=START-timedelta(days=14-d)))
    current=list(session());current[29]=replace(current[29],close=D(103),high=D('103.1'))
    prefix=history+tuple(current[:30])
    s=signal(prefix,'NOISE_BAND_LONG_V1',at=current[29].closed)
    assert s is not None
    assert signal(prefix[60:],'NOISE_BAND_LONG_V1',at=current[29].closed) is None
    assert signal(history+tuple(current[:29]),'NOISE_BAND_LONG_V1',at=current[28].closed) is None
    assert signal(tuple(replace(b,symbol='TSLA') for b in prefix),'NOISE_BAND_LONG_V1',at=current[29].closed) is None


@pytest.mark.parametrize('policy',tuple(RULES))
def test_future_or_missing_or_nonfinite_data_cannot_generate_a_signal(policy):
    bars=session()
    with pytest.raises(ValueError):signal(bars[:22],policy,at=bars[20].closed)
    with pytest.raises(ValueError):signal(bars[:5]+bars[6:22],policy,at=bars[21].closed)
    with pytest.raises(ValueError):signal((replace(bars[0],close=D('NaN')),),policy,at=bars[0].closed)
    assert signal(tuple(replace(b,volume=D(0)) for b in bars[:22]),policy,at=bars[21].closed) is None


def test_vwap_matches_direct_volume_weighted_typical_price():
    bars=session()[:2]
    bars=(bars[0],replace(bars[1],close=D('100.2'),volume=D(3000)))
    expected=sum(((b.high+b.low+b.close)*b.volume/3 for b in bars),D(0))/4000
    assert abs(vwap(bars)-expected)<D('1e-24')


def once(history,policy,*,at):
    return ReplacementSignal(policy,'SPY',at,D(99),D(1),5) if len(history)==2 else None


def test_replay_next_open_fees_stop_first_and_no_optimistic_same_bar_reentry():
    bars=session(n=5)
    bars=bars[:2]+(replace(bars[2],high=D(102),low=D(98)),)+bars[3:]
    with patch('app.equity_overhaul.replay.signal',side_effect=once):
        r=replay(bars,'VWAP_RECLAIM_SCALP_V1',Costs(D(5),D('.0035')))
    assert r['closed_trades']==1
    t=r['trades'][0]
    assert t['entry_at']==bars[2].opened.isoformat() and t['signal_at']==bars[1].closed.isoformat()
    assert t['reason']=='STOP_FIRST' and D(t['net_r'])< -1
    expected=D(99)*D('.9995')-D(100)*D('1.0005')-D('.007')
    assert D(t['net_pnl_per_share'])==expected


def test_gap_stop_worse_open_and_zero_entry_bar_volume_does_not_look_ahead():
    bars=list(session(n=5));bars[3]=replace(bars[3],open=D(97),high=D(98),low=D(96),close=D(97))
    with patch('app.equity_overhaul.replay.signal',side_effect=once):
        a=replay(tuple(bars),'VWAP_RECLAIM_SCALP_V1',Costs(D(0),D(0)))
        bars[2]=replace(bars[2],volume=D(0))
        b=replay(tuple(bars),'VWAP_RECLAIM_SCALP_V1',Costs(D(0),D(0)))
    assert a==b
    assert a['trades'][0]['reason']=='GAP_STOP' and a['trades'][0]['exit']=='97'


def test_complete_session_requirement_and_one_orb_trade_per_day():
    bars=list(session(n=30));bars[5]=replace(bars[5],close=D('100.4'),high=D('100.5'))
    bars[6]=replace(bars[6],open=D('100.4'),high=D(103),low=D(100),close=D(102))
    bars[9]=replace(bars[9],close=D('100.4'),high=D('100.5'))
    r=replay(tuple(bars),'ORB5_LONG_V1',Costs(D(0),D(0)))
    assert r['closed_trades']==1 and not r['execution_authorized']
    with pytest.raises(ValueError):replay(tuple(bars[:-1]),'ORB5_LONG_V1',Costs(D(0),D(0)))
