from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

from app.configuration import load_configuration
from app.asset_modules.crypto_desktop_capture import FLAGS, ENV, SECONDS, prepare, load_request
from app.asset_modules.daily_research import DailyResearch, configured_research
from app.asset_modules.daily_research_journal import canonical
from app.asset_modules.grok_observer_session import source_manifest, configuration_digest
from app.composition.runtime_mode import RuntimeMode


def configured():
    return load_configuration(FLAGS)


def request(tmp_path,config):
    path=tmp_path/'capture-request.json'
    doc=dict(scope='MANUAL_NATIVE_CRYPTO_OBSERVATION_V1',id='a'*32,created_at='2026-10-31T13:00:00+00:00',seconds=SECONDS,
        source_sha256=sha256(canonical(source_manifest(Path(__file__).resolve().parents[2]))).hexdigest(),
        configuration_sha256=configuration_digest(config))
    path.write_text(json.dumps(doc));return path,doc


def test_preparation_does_not_start_or_edit_configuration_and_generates_visible_launcher(tmp_path):
    repo=tmp_path/'repo';(repo/'app').mkdir(parents=True)
    (repo/'app/sentinel.py').write_text('pass\n');(repo/'.env').write_text('XAI_API_KEY=PRIVATE_SENTINEL\n')
    output=tmp_path/'capture'
    report=prepare(repo,output)
    text=(output/'Start-Atlas-Crypto-Observation.ps1').read_text()
    assert report['prepared_only'] and not report['atlas_started']
    assert report['orders']==report['network_requests']==report['grok_requests']==0
    assert "-m app.gui.app" in text and "'ATLAS_GROK_PAPER_ENTRIES_ENABLED' = 'false'" in text
    assert "'WARRIOR_FORWARD_PAPER_ENABLED' = 'false'" in text
    assert 'PRIVATE_SENTINEL' not in ''.join(p.read_text() for p in output.iterdir())
    assert (repo/'.env').read_text()=='XAI_API_KEY=PRIVATE_SENTINEL\n'
    assert not (output/'recording-session.json').exists()
    with pytest.raises(ValueError):prepare(repo,output)


@pytest.mark.parametrize('change',[
    dict(live_trading_enabled=True),dict(grok_paper_entries_enabled=True),
    dict(warrior_forward_paper_enabled=True),dict(quick_scalper_enabled=True),
    dict(crypto_native_observer_enabled=False),dict(paper_controller_enabled=False),
    dict(crypto_shared_allocation_enabled=False),dict(crypto_discovery_enabled=False),
    dict(crypto_intelligence_enabled=False),dict(crypto_completed_bar_capture_enabled=False),
])
def test_request_never_enables_entries_or_allows_other_modes(tmp_path,change):
    config=replace(configured(),**change);path,doc=request(tmp_path,config)
    with pytest.raises(ValueError):load_request(path,config,doc['source_sha256'],doc['configuration_sha256'])


def test_used_changed_or_inherited_trading_lease_is_refused(tmp_path,monkeypatch):
    c=configured();path,doc=request(tmp_path,c)
    assert load_request(path,c,doc['source_sha256'],doc['configuration_sha256'])[1]==doc
    with pytest.raises(ValueError):load_request(path,c,'b'*64,doc['configuration_sha256'])
    monkeypatch.setenv('ATLAS_GROK_EXECUTION_TRIAL','old-path')
    with pytest.raises(ValueError):load_request(path,c,doc['source_sha256'],doc['configuration_sha256'])
    monkeypatch.delenv('ATLAS_GROK_EXECUTION_TRIAL')
    path.with_name('recording-session.json').write_text('{}')
    with pytest.raises(ValueError,match='ALREADY_USED'):load_request(path,c,doc['source_sha256'],doc['configuration_sha256'])


def test_explicit_capture_refuses_silent_recorder_failure(tmp_path,monkeypatch):
    monkeypatch.setenv(ENV,str(tmp_path/'missing-request.json'))
    with pytest.raises(FileNotFoundError):configured_research(configured(),RuntimeMode.PAPER)
    with pytest.raises(ValueError):configured_research(replace(configured(),daily_research_enabled=False),RuntimeMode.PAPER)


def test_crypto_readiness_and_closed_journal_summary_are_distinct_from_equity(tmp_path,monkeypatch):
    c=configured();path,doc=request(tmp_path,c);monkeypatch.setenv(ENV,str(path))
    recorder=DailyResearch(c)
    metrics=dict(assessments_submitted=1,assessments_completed=1,entry_authority=False,entries_enabled=False,grok_requests=0)
    recorder.crypto_observer=NS(metrics=lambda:metrics,runtime=NS(metrics=lambda:dict(enabled=True,snapshot_symbols_fresh=2)),
        thread=NS(is_alive=lambda:True),protection_thread=NS(is_alive=lambda:True))
    try:
        pointer=json.loads((tmp_path/'recording-session.json').read_text())
        assert pointer['capture_id']==doc['id'] and Path(pointer['session'])==recorder.directory
        assert recorder.crypto_capture['seconds']==1800
        recorder.journal.submit('CRYPTO_NATIVE_OBSERVER_INPUT',dict(symbol='BTC/USD',execution_authorized=False))
        live=recorder._write_crypto_readiness()
        assert live['observer']==metrics and live['recording_open']
    finally:recorder.close()
    report=json.loads((recorder.directory/'research-summary.json').read_text())
    assert report['crypto_native_records']['CRYPTO_NATIVE_OBSERVER_INPUT']==1
    assert report['engine_evidence_status']=='CRYPTO_ENGINE_EVIDENCE_RECORDED'
    assert report['crypto_native_readiness']['observer']['entry_authority'] is False
    assert report['journal']['evidence_complete'] and report['journal']['writer_stopped']
    assert not report['crypto_native_readiness']['recording_open']


from tests.asset_modules.test_crypto_native_observer import native, until


def test_native_inputs_and_results_reach_journal_without_entry_or_model_authority(native,tmp_path):
    from app.asset_modules.daily_research_journal import DailyJournal,verify_journal
    observer,shared,paper,router,runtime,q=native
    journal=DailyJournal(tmp_path/'observer-journal','a'*64)
    observer._evidence_sink=journal.submit
    try:
        observer.start();until(lambda:observer.metrics()['assessments_completed']==1)
        assert not paper.snapshot()['positions'] and not router.enabled
        assert observer.protection_thread.is_alive()
        assert observer.close()
    finally:observer.close();journal.close()
    verified=verify_journal(journal.directory)
    assert verified['evidence_complete']
    rows=[json.loads(line) for p in journal.directory.glob('events-*.jsonl') for line in p.read_text().splitlines()]
    inputs=[row for row in rows if row['kind']=='CRYPTO_NATIVE_OBSERVER_INPUT']
    results=[row for row in rows if row['kind']=='CRYPTO_NATIVE_OBSERVER_RESULT']
    assert len(inputs)==len(results)==1
    assert 22<=len(inputs[0]['payload']['history']['bars'])<=48
    assert results[0]['payload']['reason']=='ENTRIES_DISABLED'
    assert results[0]['payload']['execution_authorized'] is False
    assert shared.snapshot('pool')['active_commands']==0


def test_observation_sink_failure_does_not_stop_worker_or_independent_protection(native):
    observer,_,paper,router,_,_=native
    def failed(*args):raise RuntimeError('injected writer failure')
    observer._evidence_sink=failed
    observer.start();until(lambda:observer.metrics()['assessments_completed']==1)
    assert observer.protection_thread.is_alive() and not router.enabled
    assert not paper.snapshot()['positions']


def test_real_desktop_binds_capture_and_completed_history_without_start_or_network(tmp_path,monkeypatch,historical_intelligence_artifact):
    import app.composition.desktop as desktop
    import app.composition.asset_modules as markets
    c=load_configuration({**FLAGS,'EXECUTION_DATABASE_PATH':str(tmp_path/'execution.db'),
        'WARRIOR_FORWARD_CAPTURE_PATH':str(tmp_path/'warrior.db'),
        'CRYPTO_DISCOVERY_PATH':str(tmp_path/'crypto-research.jsonl')})
    path,_=request(tmp_path,c);monkeypatch.setenv(ENV,str(path))
    monkeypatch.setattr(desktop,'load_configuration',lambda:c)
    monkeypatch.setattr('app.configuration.load_configuration',lambda:c)
    monkeypatch.setattr(markets,'configured_suite',lambda:None)
    def no_network(*a,**k):raise AssertionError('No provider request during composition')
    monkeypatch.setattr('app.webull.client_factories.MarketDataClientFactory.create',no_network)
    graph=desktop.create_desktop_composition(paper_persistence_path=tmp_path/'paper.db')
    modules=None
    try:
        modules=markets.create_asset_modules(graph)
        observer=modules.crypto_supervisor
        assert graph.daily_research.crypto_observer is observer
        assert graph.crypto_research_runtime._intelligence_context_sink is not None
        assert observer.provider is None and observer.thread is None
        assert not observer.entries_enabled and not observer.entry_router.enabled
        assert not graph.runtime_service.is_active
        report=graph.daily_research._write_crypto_readiness()
        assert report['observer']['entry_authority'] is False
        assert report['data']['history_requests_attempted']==0
        assert not report['observation_thread_alive']
    finally:
        if modules is not None:modules.crypto_supervisor.close();modules.crypto_supervisor.paper.close()
        graph.close(timeout_seconds=1)
