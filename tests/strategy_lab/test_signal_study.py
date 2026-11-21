"""Disconnected tests of candle integrity and unchanged native condition masks."""
from datetime import UTC,datetime
from types import SimpleNamespace
import json
import numpy as np
import pandas as pd
import pytest
from app.strategy_lab import signal_study as study


def test_drop_uncommitted_without_modifying_closed_candles():
    rows=[[300,10,11,9,10,10,1,3],[600,10,11,9,10,10,1,3]]
    assert study.normalize(rows,5,899)==[[300,10.,11.,9.,10.,1.]]
    assert len(study.normalize(rows,5,900))==2


@pytest.mark.parametrize('rows',[
    [[301,10,11,9,10,10,1,3]],
    [[300,10,9,9,10,10,1,3]],
    [[300,10,11,9,float('nan'),10,1,3]],
    [[300,10,11,9,10,10,-1,3]],
    [[300,10,11,9,10,10,1,3]]*2,
])
def test_invalid_candles_refused(rows):
    with pytest.raises(ValueError):study.normalize(rows,5,1000)


def test_trace_preserves_native_result_and_restores_helper():
    def reduce(conditions):return np.logical_and.reduce(conditions)
    module=SimpleNamespace(_and_entry_conditions=reduce)
    expected=pd.DataFrame({'enter_long':[0,1,0,1]})
    class Strategy:
        def populate_entry_trend(self,frame,metadata):
            long_entry_condition_index=42
            long_entry_logic=[np.array([False,True,False,True]),np.ones(4,dtype=bool)]
            assert module._and_entry_conditions(long_entry_logic).tolist()==[False,True,False,True]
            return expected
    result,counts=study.trace_signals(Strategy(),module,expected,'BTC/USD',2)
    assert result is expected and module._and_entry_conditions is reduce
    assert counts=={'eligible_rows':2,'conditions':[{'signal_id':42,'signal_rows':1,
        'component_count':2,'component_fail_rows':[1,0]}]}
    class Failed:
        def populate_entry_trend(self,*args):raise RuntimeError('native')
    with pytest.raises(RuntimeError):study.trace_signals(Failed(),module,expected,'BTC/USD',2)
    assert module._and_entry_conditions is reduce


def test_capture_is_bounded_and_freezes_failures(tmp_path,monkeypatch):
    monkeypatch.setattr(study,'verify_lab',lambda path:{})
    lab=tmp_path/'lab';lab.mkdir();(lab/'lab-manifest.json').write_text('{}')
    now=int(datetime.now(UTC).timestamp());start=(now//300-2)*300
    calls=[]
    def reader(op,params):
        calls.append(op)
        if op=='Time':return {'unixtime':now}
        if op=='AssetPairs':return {p:{'wsname':p.replace('BTC/','XBT/'),'status':'online','altname':p} for p in study.PAIRS}
        minutes=params['interval'];stamp=(now//(minutes*60)-1)*minutes*60
        return {'pair':[[stamp,10,11,9,10,10,1,3]],'last':now}
    output=tmp_path/'out';report=study.capture(lab,output,reader=reader,pause=lambda n:None)
    assert len(calls)==study.MAX_READS==42 and len(report['files'])==40
    assert report['status']=='COMPLETE' and report['grok_requests']==report['orders']==0
    with pytest.raises(FileExistsError):study.capture(lab,output,reader=reader,pause=lambda n:None)
    def fail(*a):raise TimeoutError()
    failed=tmp_path/'failed'
    with pytest.raises(TimeoutError):study.capture(lab,failed,reader=fail,pause=lambda n:None)
    assert json.loads((failed/'capture-manifest.json').read_text())['status']=='PARTIAL'


def test_component_ordinals_map_to_exact_source_lines(tmp_path):
    p=tmp_path/'strategy.py'
    p.write_text('''def populate_entry_trend(self, df, metadata):
    long_entry_logic = []
    long_entry_logic.append(True)
    if long_entry_condition_index == 42:
        long_entry_logic.append(df['x'] > 0)
        long_entry_logic.append(df['y'] < 2)
    long_entry_logic.append(df['volume'] > 0)
''')
    assert study.condition_source_lines(p)=={'42':[3,5,6,7]}
