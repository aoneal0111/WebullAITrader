from datetime import timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from app.asset_modules.crypto_signal_replay import replay, main
from app.crypto_research.bar_capture import CryptoBarCapture
from tests.asset_modules.test_crypto_history_adapter import inputs
from tests.asset_modules.test_crypto_worker import NOW


def record(at=NOW,*,cutoff=None,symbol='BTC/USD'):
    base,quote=symbol.split('/')
    return {'asset_type':'CRYPTO','canonical_symbol':symbol,'base_asset':base,'quote_asset':quote,
        'provider_symbol':base+quote,'timestamp':at.isoformat(),'decision_cutoff':(cutoff or at).isoformat(),
        'price':'101','bid':'101','ask':'101.1','research_only':True,'execution_authorized':False}


@pytest.fixture
def files(tmp_path):
    bars,_=inputs();path=tmp_path/'bars.db';quotes=tmp_path/'quotes.jsonl'
    CryptoBarCapture(path).append_many(bars,captured_at=NOW)
    def write(rows):
        quotes.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in rows))
        return path,quotes
    return path,quotes,write


def test_real_worker_assesses_saved_inputs_without_modifying_them(files):
    path,quotes,write=files;write([record(),record(),record(NOW+timedelta(seconds=1))])
    original=(path.read_bytes(),quotes.read_bytes())
    result=replay(path,quotes)
    assert result['worker_assessments']==result['proposed_assessments']==2
    assert result['unique_signal_ids']==1 and result['status_counts']['DUPLICATE_QUOTE']==1
    assert result['worker_started'] and not result['execution_enabled']
    assert result['orders']==result['network_requests']==0 and result['promotion']=='NONE'
    assert result['at_eof'] and result['next_offset']==len(quotes.read_bytes())
    assert original==(path.read_bytes(),quotes.read_bytes())


def test_bars_captured_later_are_not_visible_and_do_not_start_worker(tmp_path):
    path=tmp_path/'bars.db';quotes=tmp_path/'quotes.jsonl';bars,_=inputs()
    CryptoBarCapture(path).append_many(bars,captured_at=NOW+timedelta(seconds=1))
    quotes.write_text(json.dumps(record())+'\n')
    result=replay(path,quotes)
    assert result['status_counts']=={'NO_COMPLETED_PROVIDER_BARS':1}
    assert result['worker_assessments']==0 and not result['worker_started']


@pytest.mark.parametrize('case,status',[('stale','QUOTE_UNAVAILABLE'),('future','INVALID_QUOTE_RECORD'),
    ('other','OTHER_SYMBOL'),('missing','INVALID_QUOTE_RECORD'),('not_research','INVALID_QUOTE_RECORD'),
    ('number','INVALID_QUOTE_RECORD'),('naive','INVALID_QUOTE_RECORD')])
def test_ineligible_or_invalid_quote_never_starts_worker(files,case,status):
    path,quotes,write=files;r=record()
    if case=='stale':r=record(NOW-timedelta(seconds=3),cutoff=NOW)
    elif case=='future':r=record(NOW+timedelta(seconds=1),cutoff=NOW)
    elif case=='other':r=record(symbol='ETH/USD')
    elif case=='missing':r.pop('decision_cutoff')
    elif case=='not_research':r['execution_authorized']=True
    elif case=='number':r['bid']=101
    else:r['decision_cutoff']=NOW.replace(tzinfo=None).isoformat()
    write([r]);result=replay(path,quotes)
    assert result['status_counts']=={status:1} and not result['worker_started']
    assert result['worker_assessments']==0 and result['orders']==0


def test_out_of_order_cutoff_is_skipped_within_batch(files):
    path,quotes,write=files;write([record(),record(NOW-timedelta(seconds=1)),record(NOW+timedelta(seconds=1))])
    result=replay(path,quotes)
    assert result['status_counts']['OUT_OF_ORDER_CUTOFF']==1
    assert result['worker_assessments']==2


def test_scan_bounds_and_exact_byte_resume(files):
    path,quotes,write=files;write([record(symbol='ETH/USD'),record(),record(NOW+timedelta(seconds=1))])
    first=replay(path,quotes,max_scan=1)
    assert first['scanned_rows']==1 and not first['at_eof'] and not first['worker_started']
    second=replay(path,quotes,max_assessments=1,offset=first['next_offset'])
    assert second['scanned_rows']==1 and second['worker_assessments']==1 and not second['at_eof']
    third=replay(path,quotes,offset=second['next_offset'])
    assert third['scanned_rows']==1 and third['at_eof']


def test_partial_trailing_record_is_preserved_for_later_read(files):
    path,quotes,_=files;quotes.write_text(json.dumps(record()))
    result=replay(path,quotes)
    assert result['next_offset']==0 and not result['at_eof'] and not result['worker_started']
    assert result['status_counts']=={'INCOMPLETE_TRAILING_RECORD':1}
    with quotes.open('ab') as f:f.write(b'\n')
    assert replay(path,quotes,offset=result['next_offset'])['worker_assessments']==1


@pytest.mark.parametrize('values',[{'max_scan':True},{'max_scan':10001},{'max_assessments':101},
    {'offset':-1},{'offset':1},{'offset':1000000},{'symbol':'BTC/USDT'}])
def test_ambiguous_or_unbounded_requests_refused(files,values):
    path,quotes,write=files;write([record()])
    with pytest.raises(ValueError):replay(path,quotes,**values)


def test_oversized_record_stops_without_printing_content_or_creating_state(files):
    path,quotes,_=files;quotes.write_bytes(b'x'*16385+b'\n')
    with pytest.raises(ValueError,match='byte bound'):replay(path,quotes)
    assert not list(path.parent.glob('*controller*'))


def test_invalid_utf8_and_duplicate_fields_are_counted(files):
    path,quotes,_=files
    raw=json.dumps(record()).replace('"asset_type": "CRYPTO"','"asset_type": "CRYPTO", "asset_type": "CRYPTO"')
    quotes.write_bytes(b'\xff\n'+raw.encode()+b'\n')
    result=replay(path,quotes)
    assert result['status_counts']=={'INVALID_QUOTE_RECORD':2} and not result['worker_started']


def test_missing_inputs_cli_is_read_only(tmp_path,capsys):
    bars,quotes=tmp_path/'absent.db',tmp_path/'absent.jsonl'
    assert main(['--bars',str(bars),'--quotes',str(quotes)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='MISSING_INPUTS' and result['orders']==0
    assert not bars.exists() and not quotes.exists()


def test_foreign_bar_database_fails_without_modification(files,capsys):
    path,quotes,write=files;write([record()])
    foreign=path.with_name('foreign.db')
    with sqlite3.connect(foreign) as db:db.execute('CREATE TABLE positions (symbol TEXT)')
    before=foreign.read_bytes()
    assert main(['--bars',str(foreign),'--quotes',str(quotes)])==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='REPLAY_UNAVAILABLE' and 'error_class' in result
    assert foreign.read_bytes()==before


def test_runtime_research_record_schema_is_compatible(files,tmp_path):
    from app.crypto_research.runtime import CryptoResearchRuntime
    import time
    path,quotes,write=files;_,q=inputs()
    runtime=CryptoResearchRuntime(enabled=True,path=tmp_path/'runtime.jsonl',clock=lambda:NOW)
    runtime.start()
    try:
        assert runtime.admit(q)
        deadline=time.monotonic()+3
        while not runtime.latest() and time.monotonic()<deadline:time.sleep(.01)
        write([runtime.latest()[0].to_record()])
        result=replay(path,quotes)
        assert result['worker_assessments']==1 and result['proposed_assessments']==1
        assert result['orders']==0
    finally:runtime.close()
