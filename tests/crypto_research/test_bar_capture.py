from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import sqlite3

import pytest

from app.crypto_research.bar_capture import CryptoBarCapture, read_bars
from app.crypto_research.evidence import CryptoBarInterval
from app.crypto_research.runtime import CryptoResearchRuntime
from tests.asset_modules.test_crypto_history_adapter import inputs
from tests.asset_modules.test_crypto_worker import NOW


def test_lazy_capture_restart_first_seen_and_read_only(tmp_path):
    path=tmp_path/'bars.db';bars,_=inputs();capture=CryptoBarCapture(path)
    assert not path.exists()
    assert capture.append_many(bars,captured_at=NOW).stored==22
    after=tuple(replace(b,observed_at=NOW+timedelta(seconds=1),decision_cutoff=NOW+timedelta(seconds=1)) for b in bars)
    duplicate=CryptoBarCapture(path).append_many(after,captured_at=NOW+timedelta(seconds=1))
    assert duplicate.stored==0 and duplicate.duplicates==22
    before=path.read_bytes()
    assert read_bars(path,'BTC/USD',as_of=NOW-timedelta(microseconds=1))==()
    assert read_bars(path,'BTC/USD',as_of=NOW)==bars
    assert read_bars(path,'BTC/USD',as_of=NOW,limit=5)==bars[-5:]
    assert path.read_bytes()==before


def test_durable_capture_time_is_not_backdated_to_provider_observation(tmp_path):
    bars,_=inputs();path=tmp_path/'bars.db'
    CryptoBarCapture(path).append_many(bars,captured_at=NOW+timedelta(seconds=1))
    assert read_bars(path,'BTC/USD',as_of=NOW)==()
    assert read_bars(path,'BTC/USD',as_of=NOW+timedelta(seconds=1))==bars


def test_numeric_formatting_is_not_a_provider_revision(tmp_path):
    bars,_=inputs();capture=CryptoBarCapture(tmp_path/'bars.db')
    capture.append_many(bars,captured_at=NOW)
    equivalent=tuple(replace(b,close=b.close.quantize(D('.000'))) for b in bars)
    assert capture.append_many(equivalent,captured_at=NOW).duplicates==22


def test_revision_rolls_back_whole_batch_and_preserves_original(tmp_path):
    bars,_=inputs();path=tmp_path/'bars.db';capture=CryptoBarCapture(path)
    capture.append_many(bars[-1:],captured_at=NOW)
    changed=replace(bars[-1],high=bars[-1].high+D(1))
    with pytest.raises(ValueError,match='revision'):capture.append_many(bars[:-1]+(changed,),captured_at=NOW)
    assert read_bars(path,'BTC/USD',as_of=NOW)==bars[-1:]


def test_capacity_is_bounded_immutable_and_does_not_delete(tmp_path):
    bars,_=inputs();path=tmp_path/'bars.db';capture=CryptoBarCapture(path,capacity=2)
    capture.append_many(bars[-2:],captured_at=NOW)
    with pytest.raises(ValueError,match='capacity reached'):capture.append_many(bars[:1],captured_at=NOW)
    with pytest.raises(ValueError,match='immutable'):CryptoBarCapture(path,capacity=3).append_many(bars[:1],captured_at=NOW)
    assert read_bars(path,'BTC/USD',as_of=NOW)==bars[-2:]


@pytest.mark.parametrize('case',['foreign','marker'])
def test_foreign_sqlite_rejected_without_modification(tmp_path,case):
    path=tmp_path/'foreign.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE positions (symbol TEXT)')
        if case=='marker':db.execute('PRAGMA application_id=1096041296')
    before=path.read_bytes();bars,_=inputs()
    with pytest.raises(ValueError,match='Dedicated'):CryptoBarCapture(path).append_many(bars,captured_at=NOW)
    assert path.read_bytes()==before
    with pytest.raises(ValueError,match='Dedicated'):read_bars(path,'BTC/USD',as_of=NOW)
    assert path.read_bytes()==before


def test_missing_read_never_creates_file(tmp_path):
    path=tmp_path/'missing.db'
    with pytest.raises(sqlite3.OperationalError):read_bars(path,'BTC/USD',as_of=NOW)
    assert not path.exists()


def test_concurrent_duplicate_batches_store_once(tmp_path):
    path=tmp_path/'bars.db';bars,_=inputs()
    # Establish schema, then concurrent independent capture handles.
    CryptoBarCapture(path).append_many(bars[:1],captured_at=NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:CryptoBarCapture(path).append_many(bars,captured_at=NOW),range(2)))
    assert sum(r.stored for r in results)==21
    assert len(read_bars(path,'BTC/USD',as_of=NOW))==22


def test_corrupt_payload_refuses_readback(tmp_path):
    path=tmp_path/'bars.db';bars,_=inputs();CryptoBarCapture(path).append_many(bars,captured_at=NOW)
    with sqlite3.connect(path) as db:db.execute("UPDATE completed_bars SET payload=replace(payload,'101.2','102.2')")
    with pytest.raises(ValueError,match='fingerprint'):read_bars(path,'BTC/USD',as_of=NOW)


@pytest.mark.parametrize('values',[{'captured_at':NOW-timedelta(seconds=1)}, {'captured_at':NOW.replace(tzinfo=None)},
    {'bars':[]},{'bars':(object(),)},{'bars':inputs()[0]*3}])
def test_invalid_capture_input_refused_before_file_creation(tmp_path,values):
    bars,_=inputs();path=tmp_path/'bars.db'
    with pytest.raises(ValueError):CryptoBarCapture(path).append_many(**{'bars':bars,'captured_at':NOW,**values})
    assert not path.exists()


@pytest.mark.parametrize('fails',[False,True])
def test_runtime_capture_is_optional_and_failure_is_separate_from_provider(tmp_path,fails):
    bars,q=inputs()
    class Capture(CryptoBarCapture):
        def append_many(self,*args,**kwargs):
            if fails:raise OSError('full disk')
            return super().append_many(*args,**kwargs)
    class Provider:
        def historical_bars(self,pair,**kwargs):
            return tuple({'timestamp':b.opened_at.isoformat(),'open':str(b.open),'high':str(b.high),
                'low':str(b.low),'close':str(b.close)} for b in bars)
    runtime=CryptoResearchRuntime(enabled=True,provider=Provider(),path=tmp_path/'quotes.jsonl',clock=lambda:NOW,
        intelligence_context_sink=lambda _:None,completed_bar_capture=Capture(tmp_path/'bars.db'))
    runtime._research_shortlist=lambda:(q.pair,)
    runtime._refresh_completed_bars()
    metrics=runtime.metrics()
    assert metrics.history_requests_succeeded==2 and metrics.history_requests_failed==0
    assert metrics.history_retained_bars>0
    assert metrics.completed_bar_capture_failures==(2 if fails else 0)
    # Four final rows are incomplete under M5 normalization and cannot be captured.
    assert metrics.completed_bar_capture_stored==(0 if fails else 40)
    if not fails:assert len(read_bars(tmp_path/'bars.db','BTC/USD',as_of=NOW))==22
    runtime.close()


def test_concurrent_first_batches_create_one_schema(tmp_path):
    path=tmp_path/'new.db';bars,_=inputs()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:CryptoBarCapture(path).append_many(bars,captured_at=NOW),range(2)))
    assert sum(r.stored for r in results)==22
    assert sum(r.duplicates for r in results)==22
    assert len(read_bars(path,'BTC/USD',as_of=NOW))==22


@pytest.mark.parametrize('enabled',[False,True])
def test_desktop_capture_flag_is_default_off_and_construction_is_lazy(tmp_path,monkeypatch,enabled):
    import app.composition.desktop as desktop
    from app.configuration.loader import load_configuration
    config=load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER',
        'EXECUTION_DATABASE_PATH':str(tmp_path/'execution.db'),
        'CRYPTO_DISCOVERY_PATH':str(tmp_path/'crypto.jsonl'),
        'ATLAS_CRYPTO_COMPLETED_BAR_CAPTURE_ENABLED':str(enabled).lower()})
    assert not load_configuration({}).crypto_completed_bar_capture_enabled
    monkeypatch.setattr(desktop,'load_configuration',lambda:config)
    composition=desktop.create_desktop_composition(paper_persistence_path=tmp_path/'paper.db')
    try:
        capture=composition.crypto_research_runtime._completed_bar_capture
        assert (capture is not None) is enabled
        if enabled:assert capture.path==tmp_path/'crypto-completed-bars.sqlite3'
        assert not (tmp_path/'crypto-completed-bars.sqlite3').exists()
        assert composition.crypto_research_runtime.metrics().history_requests_attempted==0
    finally:composition.close()


def test_read_only_check_reports_missing_capture_without_creating(tmp_path,capsys):
    import json
    from app.asset_modules.crypto_bar_capture_check import main
    path=tmp_path/'missing.db'
    assert main(['--path',str(path)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='MISSING_CAPTURE' and result['network_requests']==0 and result['orders']==0
    assert not path.exists()


def test_read_only_check_reports_bars_without_claiming_quote_readiness(tmp_path,capsys):
    import json
    from app.asset_modules.crypto_bar_capture_check import main
    path=tmp_path/'bars.db';bars,_=inputs();CryptoBarCapture(path).append_many(bars,captured_at=NOW)
    before=path.read_bytes()
    assert main(['--path',str(path),'--as-of',NOW.isoformat()])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='BAR_WINDOW_AVAILABLE' and result['bar_count']==22
    assert result['quote_validation']=='NOT_PERFORMED' and result['orders']==0
    assert path.read_bytes()==before
