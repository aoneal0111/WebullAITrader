"""Disconnected tests of frozen selection rules and offline-only wrappers."""
from copy import deepcopy
from types import ModuleType,SimpleNamespace
from datetime import datetime,UTC
import json
import sys
import pandas as pd
import pytest
from app.strategy_lab import candidate_screen as screen


def metrics(name,variant='CAPPED_2PCT',profit=10,fee=.004,**updates):
    return {'candidate':name,'exit_variant':variant,'fee_per_side':fee,'nonforced_closed_trades':60,
        'profit_total_abs':profit,'win_rate':.65,'profit_factor':1.4,'max_drawdown_account':.02,**updates}


def test_selection_is_capped_development_only_and_cost_stressed():
    first,second,third=[c['class'] for c in screen.LOCK['candidates']]
    rows=[metrics(first,fee=fee,profit=5) for fee in screen.FEES]
    rows.extend(metrics(second,'SOURCE_EXITS',999,fee) for fee in screen.FEES)
    rows.extend(metrics(third,fee=fee,profit=20) for fee in screen.FEES)
    audits={name:{'passed':True} for name in [first,second,third]}
    assert screen.select_development(rows,audits)==third
    audits[third]['passed']=False
    assert screen.select_development(rows,audits)==first
    rows[1]['profit_total_abs']=-1
    assert screen.select_development(rows,audits) is None


@pytest.mark.parametrize('field,value',[
    ('nonforced_closed_trades',49),('profit_total_abs',0),('win_rate',.54),
    ('profit_factor',1.19),('max_drawdown_account',.051)])
def test_high_win_rate_cannot_override_cost_risk_or_sample_failure(field,value):
    rows=[metrics('Strategy001',fee=fee) for fee in screen.FEES]
    rows[0][field]=value
    assert not screen.passes(rows,50)


def test_source_hashes_verified_before_execution(tmp_path,monkeypatch):
    lock=deepcopy(screen.LOCK)
    for candidate in lock['candidates']:
        path=tmp_path/candidate['path'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('# invented source fixture\n');candidate['sha256']=screen.digest(path)
    (tmp_path/'LICENSE').write_text('invented license');lock['license_sha256']=screen.digest(tmp_path/'LICENSE')
    monkeypatch.setattr(screen,'LOCK',lock)
    screen.verify_source(tmp_path)
    (tmp_path/lock['candidates'][0]['path']).write_text('changed')
    with pytest.raises(ValueError,match='PINNED'):screen.verify_source(tmp_path)


def test_candidate_wrapper_refuses_live_and_dry_run(tmp_path,monkeypatch):
    module=ModuleType('Strategy001')
    class Base:
        def __init__(self,c):self.config=c
    module.Strategy001=Base;monkeypatch.setitem(sys.modules,'Strategy001',module)
    namespace={};exec(screen.wrapper_text(screen.LOCK['candidates'][0]),namespace)
    cls=namespace['AtlasStrategy001Screen']
    (tmp_path/'OFFLINE_CANDIDATE_ONLY').write_text('test')
    for mode in ['live','dry_run']:
        with pytest.raises(ValueError):cls({'runmode':SimpleNamespace(value=mode),'dry_run':True,'user_data_dir':tmp_path})
    native=cls({'runmode':SimpleNamespace(value='backtest'),'dry_run':True,'user_data_dir':tmp_path})
    assert native.startup_candle_count==400 and not native.can_short and not native.position_adjustment_enable


def test_prefix_audit_detects_future_reads(tmp_path):
    data=tmp_path/'candles';data.mkdir()
    dates=pd.date_range('2026-05-01','2026-10-01',freq='1h',tz='UTC')
    rows=[[int(d.timestamp()*1000),10,11,9,10,1] for d in dates]
    for pair in ['BTC_USD','XRP_USD']:(data/(pair+'-1h.json')).write_text(json.dumps(rows))
    class Strategy:
        future=False
        def populate_indicators(self,df,metadata):
            df['test']=df['close'].shift(-1) if self.future else df['close'].rolling(3).mean()
            return df
        def populate_entry_trend(self,df,metadata):return df
        def populate_exit_trend(self,df,metadata):return df
    candidate=Strategy()
    assert screen.audit_prefix(candidate,tmp_path,'1h')['passed']
    candidate.future=True
    report=screen.audit_prefix(candidate,tmp_path,'1h')
    assert not report['passed'] and len(report['checks'])==6


def test_protocol_is_frozen_without_activation():
    p=screen.protocol()
    assert p['development']=='20260701-20260901' and p['validation']=='20260901-20261001'
    assert p['fees_per_side']==[.004,.006] and p['max_open_trades']==2
    assert not p['activation_authorized']
