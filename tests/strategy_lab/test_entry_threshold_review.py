from datetime import date,timedelta
from types import SimpleNamespace
import math
import pytest
from app.strategy_research.source_candidate_lab import signals,features


def test_entry_relaxation_adds_only_entries_and_preserves_exit_flags_and_prefixes():
    closes=[100+i*.1 for i in range(215)]+[121,120.8,120.6,120.4,120.2,120,119.8,120.5]
    bars={(date(2024,1,1)+timedelta(days=i)).isoformat():dict(open=c,high=c+1,low=c-1,close=c,volume=100)
          for i,c in enumerate(closes)}
    rows={'SPY':bars,'QQQ':bars}
    for mode,default in [('RSI2_SPY_REGIME',5),('CONNORS_RSI2',10)]:
        baseline=signals(rows,mode);same=signals(rows,mode,entry_rsi=default)
        loose=signals(rows,mode,entry_rsi=20)
        assert baseline==same
        assert all(baseline[k][1:]==loose[k][1:] for k in baseline)
        assert all(not v[0] or loose[k][0] for k,v in baseline.items())
        assert sum(v[0] for v in loose.values())>sum(v[0] for v in baseline.values())
        cut=sorted(bars)[-3]
        prefix=signals({s:{d:b for d,b in bars.items() if d<=cut} for s in rows},mode,entry_rsi=20)
        assert prefix=={k:v for k,v in loose.items() if k[0]<=cut}
    for bad in [math.nan,math.inf,0,31]:
        with pytest.raises(ValueError,match='BOUNDED'):signals(rows,'CONNORS_RSI2',entry_rsi=bad)


def test_crypto_snapshot_reports_actual_parameters_without_changing_signal():
    pd=pytest.importorskip('pandas')
    from app.strategy_lab.crypto_fleet_runtime import snapshot
    frame=pd.DataFrame([dict(date=pd.Timestamp('2026-11-09',tz='UTC'),enter_long=0,
        close=100.,rsi=math.nan,sma200=99.,roc3=-.015)])
    original=frame.copy(deep=True)
    result=snapshot(frame,strategy=SimpleNamespace(low_offset=SimpleNamespace(value=.944)))
    assert result['state']=='NO_SOURCE_BUY_SIGNAL'
    assert result['entry_inputs']['roc3']==-.015
    assert 'rsi' not in result['entry_inputs']
    assert result['entry_parameters']=={'low_offset':.944}
    pd.testing.assert_frame_equal(frame,original)


def test_separate_entry_case_buys_the_extra_signal_and_keeps_restart_idempotency(tmp_path):
    from datetime import UTC,datetime
    from app.strategy_lab.equity_daily_paper import ENTRY_CASES,database,cycle,status
    end=date(2026,11,6);closes=[100+i*.1 for i in range(220)]+[121.1]
    bars={(end-timedelta(days=220-i)).isoformat():dict(open=c,high=c+1,low=c-1,close=c,volume=100)
        for i,c in enumerate(closes)}
    now=datetime(2026,11,9,15,tzinfo=UTC)
    rows={'SPY':bars,'QQQ':bars}
    quotes={s:dict(price=121.1,at=now.isoformat(),fresh_regular_session=True,market_open=True) for s in rows}
    cases=tuple(ENTRY_CASES);path=tmp_path/'paper.sqlite3'
    db=database(path,cases=cases);result=cycle(db,rows,quotes,now,cases=cases)
    assert {e['strategy'] for e in result['orders']}=={'CONNORS_RSI2_R12_5'}
    assert len(result['orders'])==2
    for case in ('CONNORS_RSI2','RSI2_SPY_REGIME'):
        assert status(db,cases=cases)[case]['cash']==10000
    assert result['entry_conditions']['CONNORS_RSI2_R12_5|QQQ']['rsi_pass']
    assert not result['entry_conditions']['CONNORS_RSI2|QQQ']['rsi_pass']
    db.close();db=database(path,cases=cases)
    assert not cycle(db,rows,quotes,now,cases=cases)['orders']
    assert status(db,cases=cases)['CONNORS_RSI2_R12_5']['simulated_orders']==2
    db.close()


def test_entry_pilot_creates_manual_only_proxy_with_read_only_source(tmp_path):
    import json
    from app.strategy_lab.equity_entry_pilot import prepare
    root=tmp_path/'pilot';prepare(root,tmp_path/'source')
    compose=json.loads((root/'compose.json').read_text())
    service=compose['services']['equity-entry']
    assert service['command'][-1]=='--entry-experiments'
    assert service['read_only'] and service['volumes'][0]['read_only']
    assert not list(root.rglob('*.sqlite3'))
    assert (root/'Start-Equity-Entry-Test.ps1').is_file()
