from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
pytest.importorskip('freqtrade',reason='Isolated native research environment required')
from app.strategy_lab.AtlasIncomeEMA import AtlasIncomeEMA,AtlasIncomeEMACausal


class FakeTrade:
    pair='BTC/USD';open_rate=100.;fee_open=.004;fee_close=.004
    open_date_utc=datetime(2026,8,20,12,tzinfo=UTC)
    def __init__(self):self.data={}
    def get_custom_data(self,key,default=None):return self.data.get(key,default)
    def set_custom_data(self,key,value):self.data[key]=value


def instance():
    s=AtlasIncomeEMA({});trade=FakeTrade()
    frame=pd.DataFrame(dict(date=pd.to_datetime(['2026-08-20T08:00Z','2026-08-20T12:00Z']),
        signal_atr=[5.,999.],close=[100.,200.],volume=[10.,1e9]))
    s.dp=SimpleNamespace(get_analyzed_dataframe=lambda *a,**k:(frame,None))
    return s,trade,frame


def test_entry_atr_never_uses_entry_or_future_candle():
    s,t,f=instance();assert s._entry_atr(t.pair,t)==5.
    f.loc[0,'signal_atr']=1000.
    assert s._entry_atr(t.pair,t)==5.


def test_initial_stop_is_signal_atr_anchored():
    s,t,_=instance()
    ratio=s.custom_stoploss(t.pair,t,t.open_date_utc,120.,.2,False)
    assert 120*(1-ratio)==pytest.approx(85.)


def test_native_net_roi_recovers_physical_atr_target_with_both_fees():
    s,t,_=instance()
    roi=s.custom_roi(t.pair,t,t.open_date_utc,0,None,'long')
    target=t.open_rate*(1+t.fee_open)*(1+roi)/(1-t.fee_close)
    assert target==pytest.approx(140.)


def test_timeout_starts_at_the_actual_entry_time():
    s,t,_=instance()
    assert s.custom_exit(t.pair,t,t.open_date_utc+timedelta(hours=167),100.,0.) is None
    assert s.custom_exit(t.pair,t,t.open_date_utc+timedelta(hours=168),100.,0.)=='INCOME_168H_TIMEOUT'


def test_missing_signal_atr_refuses_callback_instead_of_inventing_risk():
    s,t,f=instance();f=f.drop(index=0)
    s.dp=SimpleNamespace(get_analyzed_dataframe=lambda *a,**k:(f,None))
    with pytest.raises(ValueError,match='COMPLETED_SIGNAL'):
        s._entry_atr(t.pair,t)


def test_entry_sizing_uses_completed_volume_not_future_volume(monkeypatch):
    from freqtrade.persistence import Trade
    s,t,f=instance();s.config={'stake_currency':'USD'}
    s.wallets=SimpleNamespace(get_free=lambda currency:6000.)
    monkeypatch.setattr(Trade,'get_trades_proxy',lambda **kwargs:[])
    stake=s.custom_stake_amount(t.pair,t.open_date_utc,100.,1000.,1.,5000.,1.,None,'long')
    assert stake==pytest.approx(10.)


def test_standalone_native_entry_and_exit_signals_match_screen_formula():
    pytest.importorskip('numba')
    from app.strategy_lab.daily_income_lab import Spec,features,signals
    n=700;rng=np.random.default_rng(23)
    close=100*np.exp(np.cumsum(rng.normal(0,.025,n)))
    raw=pd.DataFrame(dict(date=pd.date_range('2026-01-01',periods=n,freq='4h',tz='UTC'),
        open=close,high=close*1.02,low=close*.98,close=close,volume=np.full(n,10000.)))
    s=AtlasIncomeEMA({});f=s.populate_indicators(raw.copy(),{'pair':'BTC/USD'})
    f=s.populate_entry_trend(f,{'pair':'BTC/USD'});f=s.populate_exit_trend(f,{'pair':'BTC/USD'})
    reference=features(raw);reference['eligible']=reference.date.diff().eq(pd.Timedelta(hours=4)).rolling(200,min_periods=200).sum().eq(200)
    b,x=signals(reference,Spec('EMA_CROSS',50,3.,8.,168))
    np.testing.assert_array_equal(f.enter_long.to_numpy(dtype=bool),b)
    np.testing.assert_array_equal(f.exit_long.to_numpy(dtype=bool),x)


def test_same_bar_intrabar_exit_does_not_free_a_slot_for_open_fill(monkeypatch):
    from freqtrade.persistence import Trade
    now=datetime(2026,8,21,tzinfo=UTC)
    ghost=SimpleNamespace(close_date_utc=now,exit_reason='roi')
    monkeypatch.setattr(Trade,'get_trades_proxy',lambda is_open:[object()]*5 if is_open else [ghost])
    s=AtlasIncomeEMACausal({})
    assert not s.confirm_trade_entry('VET/USD','market',1.,100.,'GTC',now,None,'long')
    assert s.causal_slot_rejections==1


def test_prior_bar_exit_does_free_a_slot(monkeypatch):
    from freqtrade.persistence import Trade
    now=datetime(2026,8,21,tzinfo=UTC)
    ghost=SimpleNamespace(close_date_utc=now-timedelta(hours=4),exit_reason='roi')
    monkeypatch.setattr(Trade,'get_trades_proxy',lambda is_open:[object()]*5 if is_open else [ghost])
    assert AtlasIncomeEMACausal({}).confirm_trade_entry('VET/USD','market',1.,100.,'GTC',now,None,'long')


def test_planned_open_exit_is_not_treated_as_intrabar_capital(monkeypatch):
    from freqtrade.persistence import Trade
    now=datetime(2026,8,21,tzinfo=UTC)
    closed=SimpleNamespace(close_date_utc=now,exit_reason='exit_signal')
    monkeypatch.setattr(Trade,'get_trades_proxy',lambda is_open:[object()]*5 if is_open else [closed])
    assert AtlasIncomeEMACausal({}).confirm_trade_entry('VET/USD','market',1.,100.,'GTC',now,None,'long')


def test_intrabar_proceeds_cannot_finance_a_new_open_order(monkeypatch):
    from freqtrade.persistence import Trade
    s,trade,frame=instance();frame.loc[0,'volume']=1e9
    causal=AtlasIncomeEMACausal({'stake_currency':'USD'});causal.dp=s.dp
    causal.wallets=SimpleNamespace(get_free=lambda currency:210.)
    ghost=SimpleNamespace(pair='BTC/USD',amount=2.,close_rate=100.,fee_close=0.,
                          open_rate=100.,close_date_utc=trade.open_date_utc,exit_reason='roi')
    monkeypatch.setattr(Trade,'get_trades_proxy',lambda is_open:[] if is_open else [ghost])
    stake=causal.custom_stake_amount('BTC/USD',trade.open_date_utc,100.,1000.,1.,5000.,1.,None,'long')
    assert stake==pytest.approx(10.)


def test_marked_risk_counts_open_losses_with_profitable_closed_trade(tmp_path):
    pytest.importorskip('numba')
    from app.strategy_lab.native_income_ema_review import marked_metrics
    folder=tmp_path/'data';folder.mkdir()
    pd.DataFrame(dict(date=pd.to_datetime(['2026-08-15T00:00Z','2026-08-15T04:00Z','2026-08-16T00:00Z']),
                      close=[100.,80.,110.])).to_feather(folder/'BTC_USD-4h.feather')
    trade=dict(pair='BTC/USD',amount=1.,open_rate=100.,close_rate=110.,fee_open=0.,fee_close=0.,
               open_date='2026-08-15T00:00Z',close_date='2026-08-16T00:00Z',profit_abs=10.,orders=[{},{}])
    stats=dict(trades=[trade],profit_total_abs=10.)
    r=marked_metrics(tmp_path,stats,1000.)
    assert r['maximum_marked_drawdown']==pytest.approx(.02)
    assert r['worst_net_marked_day']==pytest.approx(-22.)
    assert r['ledger_error']==pytest.approx(0.)
    with pytest.raises(ValueError,match='LEDGER_MISMATCH'):
        marked_metrics(tmp_path,dict(stats,profit_total_abs=11.),1000.)
