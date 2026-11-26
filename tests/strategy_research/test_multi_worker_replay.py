from dataclasses import replace
import pytest
from app.strategy_research.crypto_screening import Bar
from app.strategy_research.multi_worker_replay import DAY, Worker, replay, workers_for


def fixture(symbols=('AAA',), count=210):
    return {s:tuple(Bar(i*DAY,100.,101.,99.,100.,10000.) for i in range(count)) for s in symbols}


def decisions(data, buy=(200,), sell=(202,), stop=90.):
    return {(s,f):tuple((i in buy,i in sell,stop) for i in range(len(b)))
            for s,b in data.items() for f in ('RSI2_TREND','DONCHIAN20_TREND')}


def run(data, workers=None, **kwargs):
    return replay(data, workers or workers_for(data), start=201*DAY,end=210*DAY,
                  decisions=kwargs.pop('decisions',decisions(data)), **kwargs)


def test_hundred_duplicate_workers_do_not_multiply_exposure_or_profits():
    data=fixture()
    one=(Worker('0','AAA','RSI2_TREND'),)
    hundred=tuple(Worker(str(i),'AAA','RSI2_TREND') for i in range(100))
    a,b=run(data,one),run(data,hundred)
    assert a['trades']==b['trades']
    assert b['closed_trades']==1
    assert b['rejections']['SYMBOL_ALREADY_OWNED_OR_EXITED']==99
    assert b['ending_liquidation_equity']==a['ending_liquidation_equity']


def test_capital_and_all_fills_reconcile_with_hundred_workers():
    data=fixture(tuple(f'S{i:02}' for i in range(50)))
    result=run(data)
    assert result['workers']==100
    assert result['minimum_cash']>=0
    assert abs(result['accounting_residual'])<1e-7
    assert result['maximum_open_gross_fraction']<=1
    assert 1 <= result['maximum_simultaneous_positions'] <= len(data)
    assert all(t['debit'] <= 1000.000001 for t in result['trades'])
    assert result['ending_liquidation_equity']==pytest.approx(10000+result['realized_net'])
    assert sum(x['closed_trades'] for x in result['per_worker'].values())==result['closed_trades']


def test_higher_costs_reduce_same_trade_return():
    data=fixture()
    low=run(data,fee=0,slippage=0)
    high=run(data,fee=.002,slippage=.002)
    assert high['realized_net']<low['realized_net']
    assert high['closed_trades']==low['closed_trades']


def test_open_positions_are_marked_separately_and_not_wins():
    data=fixture()
    result=run(data,decisions=decisions(data,sell=()))
    assert result['closed_trades']==0 and result['win_rate'] is None
    assert result['open_positions']==1
    assert result['marked_net']==pytest.approx(result['unrealized_net'])


def test_gap_stop_fills_at_worse_open():
    data=fixture()
    bars=list(data['AAA']);bars[202]=Bar(202*DAY,80.,82.,79.,81.,10000.)
    result=run({'AAA':tuple(bars)})
    assert result['trades'][0]['reason']=='GAP_STOP'
    assert result['trades'][0]['exit']<80


def test_intrabar_exit_cash_cannot_fund_earlier_open():
    data=fixture(('AAA','BBB'))
    bars=list(data['AAA']);bars[202]=replace(bars[202],low=80.)
    data['AAA']=tuple(bars)
    d=decisions(data,buy=(),sell=())
    for f in ('RSI2_TREND','DONCHIAN20_TREND'):
        d['AAA',f]=tuple((i==200,False,90.) for i in range(210))
        d['BBB',f]=tuple((i==201,False,90.) for i in range(210))
    result=run(data,decisions=d,risk_fraction=1,allocation_fraction=1)
    assert result['maximum_simultaneous_positions']==1
    assert not any(t['symbol']=='BBB' for t in result['trades'])
    assert result['rejections']['INSUFFICIENT_SHARED_CASH']>=1


def test_missing_or_duplicate_bars_fail_instead_of_filling_gaps():
    data=fixture();data['AAA']=data['AAA'][:205]+data['AAA'][206:]
    with pytest.raises(ValueError,match='missing'):
        run(data)


def test_mismatched_calendars_fail():
    data=fixture(('AAA','BBB'));data['BBB']=data['BBB'][1:]
    with pytest.raises(ValueError,match='CALENDARS'):
        run(data)


def test_future_candles_cannot_change_earlier_trades():
    data=fixture(count=500)
    # Deterministic oscillation creates naturally computed signals after warmup.
    import math
    data={'AAA':tuple(Bar(i*DAY,100+i*.03+math.sin(i/3)*3,
             101+i*.03+math.sin(i/3)*3,99+i*.03+math.sin(i/3)*3,
             100+i*.03+math.sin(i/3)*3,10000.) for i in range(500))}
    settings=dict(start=220*DAY,end=350*DAY)
    a=replay(data,workers_for(data),**settings)
    changed={'AAA':tuple(replace(b,open=b.open*5,high=b.high*5,low=b.low*5,close=b.close*5)
                         if i>=350 else b for i,b in enumerate(data['AAA']))}
    b=replay(changed,workers_for(changed),**settings)
    assert a==b


def test_entry_uses_next_open_not_signal_close():
    data=fixture();bars=list(data['AAA']);bars[201]=Bar(201*DAY,110,111,109,110,10000)
    result=run({'AAA':tuple(bars)})
    assert result['trades'][0]['entry']==pytest.approx(110*1.001)
    assert result['trades'][0]['signal_at']<result['trades'][0]['entry_at']


@pytest.mark.parametrize('setting,value',[('fee',float('nan')),('slippage',-1),
    ('initial_cash',0),('risk_fraction',2),('allocation_fraction',0)])
def test_invalid_economics_fail(setting,value):
    with pytest.raises(ValueError):run(fixture(),**{setting:value})


def test_duplicate_worker_identity_rejected():
    w=Worker('same','AAA','RSI2_TREND')
    with pytest.raises(ValueError,match='UNIQUE'):
        run(fixture(),(w,w))


def test_pinned_history_download_verifies_blob_before_accepting(tmp_path,monkeypatch):
    import hashlib
    from io import BytesIO
    from app.strategy_research import multi_worker_history as capture
    raw=b'public test candle bytes'
    blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    lock=dict(commit='a'*40,files=[dict(path='binance/AAA_USDT-1d.feather',
                                     bytes=len(raw),git_blob_sha=blob)])
    monkeypatch.setattr(capture,'urlopen',lambda *a,**kw:BytesIO(raw))
    result=capture.capture(tmp_path/'good',lock)
    assert result['status']=='VERIFIED_PINNED_HISTORY'
    assert (tmp_path/'good'/'AAA_USDT-1d.feather').read_bytes()==raw
    monkeypatch.setattr(capture,'urlopen',lambda *a,**kw:BytesIO(b'wrong'))
    with pytest.raises(ValueError,match='HASH_MISMATCH'):
        capture.capture(tmp_path/'bad',lock)
    import json
    assert json.loads((tmp_path/'bad'/'capture.json').read_text())['status']=='PARTIAL'
    assert not (tmp_path/'bad'/'AAA_USDT-1d.feather').exists()


def test_same_symbol_exit_requires_original_worker():
    data=fixture()
    d=decisions(data,sell=())
    d['AAA','DONCHIAN20_TREND']=tuple((False,i==201,90.) for i in range(210))
    result=run(data,decisions=d)
    assert result['closed_trades']==0
    assert result['open_positions']==1


@pytest.mark.parametrize('family',['RSI2_TREND','DONCHIAN20_TREND'])
def test_one_worker_matches_existing_canonical_crypto_simulator(family):
    import math
    from app.strategy_research.crypto_screening import simulate,signals
    bars=tuple(Bar(i*DAY,(v:=100+i*.1+math.sin(i/3)*3),v+.05,v-.05,v,10000.)
               for i in range(500))
    settings=dict(start=220*DAY,end=500*DAY,fee=.001,slippage=.001)
    canonical=simulate(bars,signals(bars,family),seconds=DAY,candidate=family,**settings)
    portfolio=replay({'AAA':bars},(Worker('one','AAA',family),),
                     allocation_fraction=.2,**settings)
    assert portfolio['closed_trades']==canonical['closed_trades']>0
    assert portfolio['ending_liquidation_equity']==pytest.approx(canonical['ending_equity'])
    assert portfolio['win_rate']==canonical['win_rate']
    assert [t['pnl'] for t in portfolio['trades']]==pytest.approx([t['pnl'] for t in canonical['trades']])
