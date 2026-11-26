from datetime import UTC,datetime
from importlib.util import spec_from_file_location,module_from_spec
from types import ModuleType,SimpleNamespace
from pathlib import Path
import sys
import pytest


def test_replay_wrapper_is_backtest_only_and_preserves_parent_checks(tmp_path,monkeypatch):
    upstream=ModuleType('AtlasNFILab')
    class Base:
        def __init__(self,c):self.config=c;self.calls=0
        def confirm_trade_entry(self,*a,**kw):self.calls+=1;return False
    upstream.AtlasNFILab=Base;monkeypatch.setitem(sys.modules,'AtlasNFILab',upstream)
    path=Path(__file__).resolve().parents[2]/'app/strategy_lab/AtlasNFIHistoricalReplay.py'
    spec=spec_from_file_location('replay_wrapper_test',path);m=module_from_spec(spec);spec.loader.exec_module(m)
    (tmp_path/'OFFLINE_REPLAY_ONLY').write_text('test')
    for mode in ['live','dry_run']:
        with pytest.raises(ValueError):m.AtlasNFIHistoricalReplay({'runmode':SimpleNamespace(value=mode),'user_data_dir':tmp_path})
    config={'runmode':SimpleNamespace(value='backtest'),'user_data_dir':tmp_path,'atlas_lab_id':'a'*32}
    native=m.AtlasNFIHistoricalReplay(config)
    assert native.confirm_trade_entry('BTC/USD','limit',1,100,'GTC',datetime.now(UTC),'1','long') is False
    assert native.calls==1  # Lease simulation cannot override the parent's rejection.
    config['runmode']=SimpleNamespace(value='dry_run')
    assert native.confirm_trade_entry('BTC/USD','limit',1,100,'GTC',datetime.now(UTC),'1','long') is False
    assert native.calls==1


def test_native_report_serialization_preserves_dates_and_numeric_values(tmp_path):
    import json
    import numpy as np
    from app.strategy_lab.history_replay import write
    path=tmp_path/'result.json'
    write(path,{'at':datetime(2026,7,1,tzinfo=UTC),'net':np.float64(-2.5)})
    data=json.loads(path.read_text())
    assert data=={'at':'2026-07-01T00:00:00+00:00','net':-2.5}
    with pytest.raises(ValueError):write(path,{'invalid':float('nan')})


def test_replay_signal_counts_preserve_native_frame(tmp_path,monkeypatch):
    import pandas as pd
    upstream=ModuleType('AtlasNFILab')
    class Base:
        def __init__(self,c):self.config=c
        def populate_entry_trend(self,df,metadata):return df
    upstream.AtlasNFILab=Base;monkeypatch.setitem(sys.modules,'AtlasNFILab',upstream)
    path=Path(__file__).resolve().parents[2]/'app/strategy_lab/AtlasNFIHistoricalReplay.py'
    spec=spec_from_file_location('replay_signal_test',path);m=module_from_spec(spec);spec.loader.exec_module(m)
    (tmp_path/'OFFLINE_REPLAY_ONLY').write_text('test')
    native=m.AtlasNFIHistoricalReplay({'runmode':SimpleNamespace(value='backtest'),'user_data_dir':tmp_path})
    df=pd.DataFrame({'date':pd.to_datetime(['2026-06-30','2026-07-01','2026-09-30','2026-10-01'],utc=True),
        'enter_long':[1,1,0,1],'enter_short':[0,0,1,1]})
    assert native.populate_entry_trend(df,{'pair':'BTC/USD'}) is df
    assert native.replay_signal_counts=={'BTC/USD':{'evaluated_rows':2,'long_signal_rows':1,'short_signal_rows':1}}
